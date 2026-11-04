------------------------------- MODULE design -------------------------------
(***************************************************************************)
(* Model of the candle read-through cache in design.md, rules D7.1-D7.8.  *)
(*                                                                         *)
(* Time is integer ticks. A bar is identified by its open time ts. The     *)
(* source is ground truth: a bar exists from the moment it opens at the    *)
(* source and its value is its progress, min(srcNow - ts, Step). A bar is  *)
(* final when its value is Step. A restated bar has the value Step + 1.    *)
(*                                                                         *)
(* The store holds bars (st) and coverage (cov). Coverage is the SET of    *)
(* covered ticks, which is the same thing as a union of merged half-open   *)
(* ranges [s, e) at tick resolution, so D6's merge rule is implicit.       *)
(*                                                                         *)
(* Each reader runs one call at a time:                                    *)
(*   idle -> wait (call chosen) -> fill (lock held, now sampled as t0)     *)
(*        -> read (tail + response) -> idle                                *)
(* Each Fill step is one source call plus one atomic bars-then-coverage    *)
(* write (D7.6). The clock can advance between any two steps.             *)
(*                                                                         *)
(* The Fix* flags switch each rule between "as written in design.md"       *)
(* (FALSE) and the amendment this review proposes (TRUE):                  *)
(*   FixEdge  coverage and gap clip end at horizon - step + 1, not horizon *)
(*   FixTail  the tail is a windowed read from the closed edge, and every  *)
(*            tail bar goes into the response (not only "the open bar")   *)
(*   FixTrunc a truncated answer (limit rows) covers [gap_start,           *)
(*            last_ts + 1) and the rest of the gap is re-requested in the  *)
(*            same call; a short answer covers the whole gap (this needs   *)
(*            the contract clause "short answer = complete")               *)
(*   FixCount a limit-only store hit counts only the covered run that      *)
(*            ends at the edge, and serves only that run plus the tail     *)
(*   FixExtend a limit-only call first gap-fills [newest covered tick + 1, *)
(*            edge) as a windowed fill, then runs the hit test             *)
(*   FixLock  the response read runs under the key lock, and clear takes   *)
(*            the key lock                                                 *)
(* Fault flags model source behaviour the contract does not forbid today: *)
(*   Lag > 0      the source clock (or publication) lags ours              *)
(*   RestateOn    the source corrects one closed bar per key               *)
(*   TransientOn  a source call answers empty instead of raising           *)
(*   SkipOn       a limit-only answer omits one interior bar (page drop)   *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, TLC

CONSTANTS
    Keys, Readers, NoOne,
    Step,        \* bar length in ticks
    Settle,      \* D7: settle = min(step, 60)
    MaxTime,     \* clock bound; bars open at ts in 0..MaxTime-1
    Cap,         \* per-gap source limit (5000 in D7.3)
    SrcBars,     \* [Keys -> SUBSET Ticks]: open times of bars at the source
    Windows,     \* set of <<ws, we>> a windowed call may ask
    Limits,      \* set of `limit` values a call may ask
    Kinds,       \* subset of {"win", "lim"}
    Lag, RestateOn, TransientOn, SkipOn, ClearOn,
    FixEdge, FixTail, FixTrunc, FixCount, FixExtend, FixLock

VARIABLES now, st, cov, lock, pc, call, todo, restated, bad

vars == <<now, st, cov, lock, pc, call, todo, restated, bad>>

Ticks == 0..(MaxTime - 1)
NONE  == -1
Min2(a, b) == IF a <= b THEN a ELSE b
Max(S) == CHOOSE x \in S : \A y \in S : y <= x
Min(S) == CHOOSE x \in S : \A y \in S : x <= y
Range(a, b) == {x \in Ticks : a <= x /\ x < b}
Smallest(S, n) == {x \in S : Cardinality({y \in S : y < x}) < n}
Largest(S, n)  == {x \in S : Cardinality({y \in S : y > x}) < n}

-----------------------------------------------------------------------------
(* The source: ground truth and the unwrapped provider's answers.          *)
SrcNow(t)          == t - Lag
Visible(k, t)      == {ts \in SrcBars[k] : ts <= SrcNow(t)}
ClosedAtSrc(ts, t) == ts + Step <= SrcNow(t)
SrcVal(k, ts, t)   == IF ts \in restated[k] THEN Step + 1
                      ELSE Min2(SrcNow(t) - ts, Step)
\* candles(start=ws, end=we, limit=n): oldest n in [ws, we)  (LSE head(n))
UWin(k, ws, we, n, t) == Smallest({ts \in Visible(k, t) : ws <= ts /\ ts < we}, n)
\* candles(limit=n): newest n                                 (LSE tail(n))
ULim(k, n, t)         == Largest(Visible(k, t), n)

-----------------------------------------------------------------------------
(* The wrapper's clock rules, D7.1.                                        *)
Horizon(t0)    == t0 - Settle
Closed(ts, t0) == ts + Step <= Horizon(t0)
Edge(t0)       == IF FixEdge THEN Horizon(t0) - Step + 1 ELSE Horizon(t0)

Stored(k, s)   == {ts \in Ticks : s[k][ts] # NONE}
Upsert(k, S, t) ==
    [st EXCEPT ![k] = [ts \in Ticks |-> IF ts \in S THEN SrcVal(k, ts, t)
                                                    ELSE st[k][ts]]]

Missing(k, a, b) == Range(a, b) \ cov[k]
GapEnd(M, s)     == CHOOSE e \in (s + 1)..MaxTime :
                        (\A y \in s..(e - 1) : y \in M) /\ e \notin M
Gaps(M)          == {<<s, GapEnd(M, s)>> : s \in {x \in M : (x - 1) \notin M}}

\* First tick of the maximal covered run that ends at E - 1 (E if none).
RunFrom(k, E) ==
    IF E >= 1 /\ (E - 1) \in cov[k]
    THEN CHOOSE s \in 0..(E - 1) :
            (\A y \in s..(E - 1) : y \in cov[k]) /\ (s = 0 \/ (s - 1) \notin cov[k])
    ELSE E

\* D7.4 store-hit test.
LimHit(k, n, t0) ==
    LET E == Edge(t0) IN
    /\ E >= 1 /\ (E - 1) \in cov[k]                         \* "coverage reaches horizon"
    /\ IF FixCount
       THEN Cardinality({ts \in Stored(k, st) : RunFrom(k, E) <= ts /\ ts < E}) >= n
       ELSE Cardinality(Stored(k, st)) >= n                 \* "store holds >= limit closed bars"

\* dec = 0: a limit-only call has not run its hit test yet (FixExtend).
Idle == [k |-> CHOOSE x \in Keys : TRUE, kind |-> "none",
         ws |-> 0, we |-> 0, n |-> 0, t0 |-> 0, dec |-> 1]

-----------------------------------------------------------------------------
Init ==
    /\ now = 0
    /\ st = [k \in Keys |-> [ts \in Ticks |-> NONE]]
    /\ cov = [k \in Keys |-> {}]
    /\ lock = [k \in Keys |-> NoOne]
    /\ pc = [r \in Readers |-> "idle"]
    /\ call = [r \in Readers |-> Idle]
    /\ todo = [r \in Readers |-> {}]
    /\ restated = [k \in Keys |-> {}]
    /\ bad = {}

Tick ==
    /\ now < MaxTime
    /\ now' = now + 1
    /\ UNCHANGED <<st, cov, lock, pc, call, todo, restated, bad>>

Start(r, k) ==
    /\ pc[r] = "idle"
    /\ \E n \in Limits :
         \/ /\ "win" \in Kinds
            /\ \E w \in Windows :
                 call' = [call EXCEPT ![r] = [k |-> k, kind |-> "win", ws |-> w[1],
                                              we |-> w[2], n |-> n, t0 |-> 0, dec |-> 1]]
         \/ /\ "lim" \in Kinds
            /\ call' = [call EXCEPT ![r] = [k |-> k, kind |-> "lim", ws |-> 0,
                                            we |-> 0, n |-> n, t0 |-> 0,
                                            dec |-> IF FixExtend THEN 0 ELSE 1]]
    /\ pc' = [pc EXCEPT ![r] = "wait"]
    /\ UNCHANGED <<now, st, cov, lock, todo, restated, bad>>

\* D7.6: take the key lock, sample now once, compute the gaps under the lock.
Lock(r) ==
    LET c == call[r] IN
    /\ pc[r] = "wait"
    /\ lock[c.k] = NoOne
    /\ lock' = [lock EXCEPT ![c.k] = r]
    /\ call' = [call EXCEPT ![r].t0 = now]
    /\ todo' = [todo EXCEPT ![r] =
          IF c.kind = "win"
          THEN Gaps(Missing(c.k, c.ws, Min2(c.we, Edge(now))))     \* D7.3
          ELSE IF FixExtend                                        \* amended D7.4
               THEN IF cov[c.k] = {} THEN {}
                    ELSE Gaps(Missing(c.k, Max(cov[c.k]) + 1, Edge(now)))
          ELSE IF LimHit(c.k, c.n, now) THEN {} ELSE {<<-1, -1>>}]  \* D7.4
    /\ pc' = [pc EXCEPT ![r] = "fill"]
    /\ UNCHANGED <<now, st, cov, restated>>
    /\ UNCHANGED bad

\* One source call and one atomic write (bars, then coverage).
Fill(r) ==
    LET c == call[r]
        k == c.k
        g == CHOOSE x \in todo[r] : \A y \in todo[r] : x[1] <= y[1]
        rest == todo[r] \ {g}
    IN
    /\ pc[r] = "fill"
    /\ todo[r] # {}
    /\ \E transient \in (IF TransientOn THEN BOOLEAN ELSE {FALSE}) :
       IF g[1] = -1
       THEN \* D7.4 limit-only miss: inner.candles(limit=n), cover [first_ts, horizon)
            LET full == IF transient THEN {} ELSE ULim(k, c.n, now)
                Drops == IF SkipOn /\ Cardinality(full) >= 3
                         THEN {{}} \cup {{x} : x \in full \ {Min(full), Max(full)}}
                         ELSE {{}}
            IN \E d \in Drops :
               LET ans  == full \ d
                   keep == {ts \in ans : Closed(ts, c.t0)}
                   cv   == IF ans = {} THEN {} ELSE Range(Min(ans), Edge(c.t0))
               IN /\ st' = Upsert(k, keep, now)
                  /\ cov' = [cov EXCEPT ![k] = @ \cup cv]
                  /\ todo' = [todo EXCEPT ![r] = rest]
       ELSE \* D7.3 windowed gap: inner.candles(start=g[1], end=g[2], limit=Cap)
            LET ans  == IF transient THEN {} ELSE UWin(k, g[1], g[2], Cap, now)
                keep == {ts \in ans : Closed(ts, c.t0)}
                e    == IF ans = {} THEN Min2(g[2], Edge(c.t0))                 \* D7.5 holiday
                        ELSE IF FixTrunc
                             THEN IF Cardinality(ans) = Cap
                                  THEN Min2(Min2(g[2], Max(ans) + 1), Edge(c.t0))
                                  ELSE Min2(g[2], Edge(c.t0))
                             ELSE Min2(Min2(g[2], Max(ans) + Step), Edge(c.t0)) \* as written
                more == IF FixTrunc /\ Cardinality(ans) = Cap /\ e < g[2]
                        THEN {<<e, g[2]>>} ELSE {}
            IN /\ st' = Upsert(k, keep, now)
               /\ cov' = [cov EXCEPT ![k] = @ \cup Range(g[1], e)]
               /\ todo' = [todo EXCEPT ![r] = rest \cup more]
    /\ UNCHANGED <<now, lock, pc, call, restated, bad>>

\* As written, the lock guards gap computation and fill only (D7.6).
\* With FixExtend a limit-only call runs its hit test here, after the
\* extension fill; a miss queues the limit-only fetch and stays in "fill".
EndFill(r) ==
    LET c == call[r]
        toRead == /\ pc' = [pc EXCEPT ![r] = "read"]
                  /\ lock' = IF FixLock THEN lock ELSE [lock EXCEPT ![c.k] = NoOne]
    IN
    /\ pc[r] = "fill"
    /\ todo[r] = {}
    /\ IF c.kind = "lim" /\ c.dec = 0
       THEN /\ call' = [call EXCEPT ![r].dec = 1]
            /\ IF LimHit(c.k, c.n, c.t0)
               THEN toRead /\ UNCHANGED todo
               ELSE /\ todo' = [todo EXCEPT ![r] = {<<-1, -1>>}]
                    /\ UNCHANGED <<pc, lock>>
       ELSE toRead /\ UNCHANGED <<call, todo>>
    /\ UNCHANGED <<now, st, cov, restated, bad>>

\* D7.2 live tail + the response; I3 is checked here against the unwrapped
\* provider's answer to the same call at the same instant.
Read(r) ==
    LET c == call[r]
        k == c.k
        t == now
        E == Edge(c.t0)
        reach == c.kind = "lim" \/ c.we > E
        inWin(ts) == c.kind = "lim" \/ (c.ws <= ts /\ ts < c.we)
        tailAns == IF ~reach THEN {}
                   ELSE IF FixTail THEN {ts \in Visible(k, t) : ts >= E /\ inWin(ts)}
                   ELSE Largest(Visible(k, t), 2)                     \* limit=2
        tailKeep == {ts \in tailAns : Closed(ts, c.t0)}               \* "upserts the closed bar"
        tailOpen == IF FixTail THEN tailAns \ tailKeep
                    ELSE IF tailAns = {} THEN {}
                    ELSE {ts \in tailAns \ tailKeep : ts = Max(tailAns)} \* "appends the open bar"
        st2 == Upsert(k, tailKeep, t)
        base == IF c.kind = "win"
                THEN {ts \in Stored(k, st2) : c.ws <= ts /\ ts < c.we}   \* store.read(ws, we)
                ELSE IF FixCount
                     THEN {ts \in Stored(k, st2) : RunFrom(k, E) <= ts /\ ts < E}
                     ELSE Stored(k, st2)
        pool == base \cup {ts \in tailOpen : inWin(ts)}
        served == IF c.kind = "win" THEN Smallest(pool, c.n) ELSE Largest(pool, c.n)
        val(ts) == IF ts \in tailOpen THEN SrcVal(k, ts, t) ELSE st2[k][ts]
        expect == IF c.kind = "win" THEN UWin(k, c.ws, c.we, c.n, t) ELSE ULim(k, c.n, t)
        ok == served = expect /\ \A ts \in served : val(ts) = SrcVal(k, ts, t)
    IN
    /\ pc[r] = "read"
    /\ st' = st2
    /\ bad' = IF ok THEN bad
              ELSE bad \cup {[k |-> k, kind |-> c.kind, ws |-> c.ws, we |-> c.we,
                              n |-> c.n, t0 |-> c.t0, t |-> t,
                              served |-> served, expect |-> expect]}
    /\ lock' = IF FixLock THEN [lock EXCEPT ![k] = NoOne] ELSE lock
    /\ pc' = [pc EXCEPT ![r] = "idle"]
    /\ call' = [call EXCEPT ![r] = Idle]
    /\ UNCHANGED <<now, cov, todo, restated>>

\* D7.8 clear(key). The design does not say whether it takes the key lock.
Clear(k) ==
    /\ ClearOn
    /\ FixLock => lock[k] = NoOne
    /\ st' = [st EXCEPT ![k] = [ts \in Ticks |-> NONE]]
    /\ cov' = [cov EXCEPT ![k] = {}]
    /\ UNCHANGED <<now, lock, pc, call, todo, restated, bad>>

\* Risk "Source restates history": one correction per key.
Restate(k) ==
    /\ RestateOn
    /\ restated[k] = {}
    /\ \E ts \in SrcBars[k] :
         /\ ClosedAtSrc(ts, now)
         /\ restated' = [restated EXCEPT ![k] = {ts}]
    /\ UNCHANGED <<now, st, cov, lock, pc, call, todo, bad>>

Next ==
    \/ Tick
    \/ \E r \in Readers : \/ \E k \in Keys : Start(r, k)
                          \/ Lock(r) \/ Fill(r) \/ EndFill(r) \/ Read(r)
    \/ \E k \in Keys : Clear(k) \/ Restate(k)

Spec == Init /\ [][Next]_vars

FairSpec ==
    /\ Spec
    /\ WF_vars(Tick)
    /\ \A r \in Readers : WF_vars(Lock(r) \/ Fill(r) \/ EndFill(r) \/ Read(r))
    /\ \A r \in Readers, k \in Keys : SF_vars(Start(r, k))

-----------------------------------------------------------------------------
(* Properties.                                                             *)

\* I1 as written ("no stored bar has ts + step > horizon at write time") is
\* true by construction: every write filters on Closed(ts, t0). The property
\* that matters is that no stored bar is partial AT THE SOURCE:
I1 == \A k \in Keys, ts \in Ticks : st[k][ts] # NONE => st[k][ts] >= Step

\* I2: inside coverage, the store equals the source's closed bars.
I2 == \A k \in Keys : \A ts \in cov[k] :
        /\ (ts \in SrcBars[k] /\ ClosedAtSrc(ts, now)) => st[k][ts] = SrcVal(k, ts, now)
        /\ ts \notin SrcBars[k] => st[k][ts] = NONE

\* Coverage never runs ahead of the source.
CovBehindSource == \A k \in Keys : \A ts \in cov[k] \cap SrcBars[k] : ClosedAtSrc(ts, now)

\* I3: every served frame equalled the unwrapped answer (see Read).
I3 == bad = {}

\* L1: with windowed calls only, every closed tick of every key is covered
\* eventually (the clock stops at MaxTime under WF(Tick)).
L1 == \A k \in Keys : <>(Range(0, Edge(MaxTime)) \subseteq cov[k])

Perms == Permutations(Readers)

-----------------------------------------------------------------------------
(* Source shapes used by the configs.                                      *)
\* Step = 1: key "A" has a holiday at tick 3; key "B" trades every tick.
Bars1 == [k \in Keys |-> IF k = "A" THEN {0, 1, 2, 4, 5, 6, 7} ELSE 0..7]
\* Step = 2: key "A" has a holiday at 2; key "B" trades on every grid point.
Bars2 == [k \in Keys |-> IF k = "A" THEN {0, 4, 6} ELSE {0, 2, 4, 6}]
\* Step = 2 with an off-grid session-open bar at 1 on key "A".
BarsIrr == [k \in Keys |-> IF k = "A" THEN {1, 2, 4, 6} ELSE {0, 2, 4, 6}]

\* Windows (the .cfg syntax has no tuples).
Win8  == {<<0, 8>>, <<3, 6>>}      \* full window reaching the present, one inner window
WinFull == {<<0, MaxTime>>}         \* L1 run
=============================================================================
