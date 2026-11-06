------------------------------ MODULE design_ws ------------------------------
(* D12 stream websocket lifecycle. One /api/ws handler, one provider stream
   (an async generator), one browser tab.

   MODE "old":        today's handler. One coroutine pumps items. It sees a
                      closed tab only when a send fails.
   MODE "as_written": D12 as written. A pump task and a watch task. The
                      handler waits for the first task that ends, cancels the
                      other, and its finally calls agen.aclose() at once. A
                      cancel of the handler itself goes straight to that
                      finally.
   MODE "fixed":      the finally cancels BOTH tasks, awaits BOTH, and only
                      then calls agen.aclose().
   SHUTDOWN:          TRUE lets the server cancel the handler while it waits.

   Granularity: asyncio await points. task.cancel() only sets a request; the
   CancelledError reaches the task at its next resume, and a pending cancel
   wins over a result that is ready. The provider's finally awaits (moomoo:
   await asyncio.to_thread(ctx.close)), so it takes its own step.
   "already running" is the RuntimeError that CPython 3.12, 3.14 and 3.15
   raise for aclose() or __anext__() while another call into the same
   generator has not returned. *)
EXTENDS TLC

CONSTANTS MODE, SHUTDOWN

VARIABLES client,   \* "open" | "closed"                      the browser tab
          gen,      \* "running" | "idle" | "pfinal" | "hfinal" | "closed"
          pump,     \* "anext" | "send" | "done"
          watch,    \* "recv" | "done"
          pCancel,  \* a cancel request is pending on the pump task
          wCancel,  \* a cancel request is pending on the watch task
          handler,  \* "wait" | "cancel" | "gather" | "aclose" | "closing" | "done"
          err       \* some call raised "asynchronous generator is already running"

vars == <<client, gen, pump, watch, pCancel, wCancel, handler, err>>

(* gen: "running" = an __anext__() is in progress (the pump waits inside the
   generator body); "idle" = suspended at a yield; "pfinal" = the finally runs
   in the pump task (cancel delivered inside __anext__()); "hfinal" = the
   finally runs inside the handler's aclose(); "closed" = ended, finally done. *)

TypeOK ==
  /\ client \in {"open", "closed"}
  /\ gen \in {"running", "idle", "pfinal", "hfinal", "closed"}
  /\ pump \in {"anext", "send", "done"}
  /\ watch \in {"recv", "done"}
  /\ pCancel \in BOOLEAN /\ wCancel \in BOOLEAN
  /\ handler \in {"wait", "cancel", "gather", "aclose", "closing", "done"}
  /\ err \in BOOLEAN

Init ==
  /\ client = "open"
  /\ gen = "running"                 \* the pump starts in its first __anext__()
  /\ pump = "anext"
  /\ watch = IF MODE = "old" THEN "done" ELSE "recv"   \* old has no watch task
  /\ pCancel = FALSE /\ wCancel = FALSE
  /\ handler = "wait"
  /\ err = FALSE

---------------------------------------------------------------------------
(* Environment. None of these is fair: a tick may never come (market shut). *)

ClientClose ==
  /\ client = "open" /\ client' = "closed"
  /\ UNCHANGED <<gen, pump, watch, pCancel, wCancel, handler, err>>

Tick ==                              \* the stream yields a tick
  /\ pump = "anext" /\ gen = "running" /\ ~pCancel
  /\ gen' = "idle" /\ pump' = "send"
  /\ UNCHANGED <<client, watch, pCancel, wCancel, handler, err>>

ErrItem ==                           \* the stream yields {"error": ...}; the pump sends it and breaks
  /\ pump = "anext" /\ gen = "running" /\ ~pCancel
  /\ gen' = "idle" /\ pump' = "done"
  /\ UNCHANGED <<client, watch, pCancel, wCancel, handler, err>>

StreamEnds ==                        \* the generator returns or raises
  /\ pump = "anext" /\ gen = "running" /\ ~pCancel
  /\ gen' = "closed" /\ pump' = "done"
  /\ UNCHANGED <<client, watch, pCancel, wCancel, handler, err>>

Shutdown ==                          \* the server cancels the handler while it waits
  /\ SHUTDOWN /\ handler = "wait"
  /\ CASE MODE = "old"        -> /\ ~pCancel /\ pCancel' = TRUE    \* the handler IS the pump
                                 /\ UNCHANGED handler
       [] MODE = "as_written" -> handler' = "aclose" /\ UNCHANGED pCancel
       [] MODE = "fixed"      -> handler' = "cancel" /\ UNCHANGED pCancel
  /\ UNCHANGED <<client, gen, pump, watch, wCancel, err>>

---------------------------------------------------------------------------
(* Pump task. *)

Sent ==                              \* send_json returns, or fails on a closed tab
  /\ pump = "send" /\ ~pCancel
  /\ CASE client = "closed" -> pump' = "done" /\ UNCHANGED <<gen, err>>  \* WebSocketDisconnect
       [] gen = "closed"    -> pump' = "done" /\ UNCHANGED <<gen, err>>  \* StopAsyncIteration
       [] gen = "hfinal"    -> pump' = "done" /\ err' = TRUE /\ UNCHANGED gen
       [] OTHER             -> pump' = "anext" /\ gen' = "running" /\ UNCHANGED err
  /\ UNCHANGED <<client, watch, pCancel, wCancel, handler>>

PumpCancelInGen ==                   \* CancelledError raised inside the generator; its finally starts
  /\ pump = "anext" /\ gen = "running" /\ pCancel
  /\ gen' = "pfinal"
  /\ UNCHANGED <<client, pump, watch, pCancel, wCancel, handler, err>>

PumpFinalDone ==                     \* the finally ends; CancelledError leaves the pump
  /\ gen = "pfinal"
  /\ gen' = "closed" /\ pump' = "done"
  /\ UNCHANGED <<client, watch, pCancel, wCancel, handler, err>>

PumpCancelInSend ==                  \* cancel inside send_json; the generator stays at its yield
  /\ pump = "send" /\ pCancel
  /\ pump' = "done"
  /\ UNCHANGED <<client, gen, watch, pCancel, wCancel, handler, err>>

---------------------------------------------------------------------------
(* Watch task: raw websocket.receive() until websocket.disconnect. *)

WatchRecv ==
  /\ watch = "recv" /\ client = "closed" /\ ~wCancel
  /\ watch' = "done"
  /\ UNCHANGED <<client, gen, pump, pCancel, wCancel, handler, err>>

WatchCancel ==
  /\ watch = "recv" /\ wCancel
  /\ watch' = "done"
  /\ UNCHANGED <<client, gen, pump, pCancel, wCancel, handler, err>>

---------------------------------------------------------------------------
(* Handler. *)

Wake ==                              \* asyncio.wait(FIRST_COMPLETED) returns
  /\ handler = "wait"
  /\ IF MODE = "old" THEN pump = "done" ELSE (pump = "done" \/ watch = "done")
  /\ handler' = IF MODE = "old" THEN "aclose" ELSE "cancel"
  /\ UNCHANGED <<client, gen, pump, watch, pCancel, wCancel, err>>

Cancel ==                            \* cancel every task that has not ended
  /\ handler = "cancel"
  /\ pCancel' = (pCancel \/ pump # "done")
  /\ wCancel' = (wCancel \/ watch # "done")
  /\ handler' = IF MODE = "fixed" THEN "gather" ELSE "aclose"
  /\ UNCHANGED <<client, gen, pump, watch, err>>

Gather ==                            \* await asyncio.gather(pump, watch, return_exceptions=True)
  /\ handler = "gather" /\ pump = "done" /\ watch = "done"
  /\ handler' = "aclose"
  /\ UNCHANGED <<client, gen, pump, watch, pCancel, wCancel, err>>

Aclose ==
  /\ handler = "aclose"
  /\ CASE gen \in {"running", "pfinal", "hfinal"} ->
               err' = TRUE /\ handler' = "done" /\ UNCHANGED gen
       [] gen = "idle"   -> gen' = "hfinal" /\ handler' = "closing" /\ UNCHANGED err
       [] gen = "closed" -> handler' = "done" /\ UNCHANGED <<gen, err>>
  /\ UNCHANGED <<client, pump, watch, pCancel, wCancel>>

Closed ==                            \* the finally that aclose() started ends
  /\ handler = "closing" /\ gen = "hfinal"
  /\ gen' = "closed" /\ handler' = "done"
  /\ UNCHANGED <<client, pump, watch, pCancel, wCancel, err>>

---------------------------------------------------------------------------

Fair == Sent \/ PumpCancelInGen \/ PumpFinalDone \/ PumpCancelInSend
        \/ WatchRecv \/ WatchCancel \/ Wake \/ Cancel \/ Gather \/ Aclose \/ Closed

Next == ClientClose \/ Tick \/ ErrItem \/ StreamEnds \/ Shutdown \/ Fair

Spec == Init /\ [][Next]_vars
        /\ WF_vars(Sent) /\ WF_vars(PumpCancelInGen) /\ WF_vars(PumpFinalDone)
        /\ WF_vars(PumpCancelInSend) /\ WF_vars(WatchRecv) /\ WF_vars(WatchCancel)
        /\ WF_vars(Wake) /\ WF_vars(Cancel) /\ WF_vars(Gather)
        /\ WF_vars(Aclose) /\ WF_vars(Closed)

---------------------------------------------------------------------------
(* Properties. *)

NoAlreadyRunning == ~err

\* When the handler returns, the provider's finally has run.
ReleasedOnReturn == handler = "done" => gen = "closed"

\* No task outlives the handler.
NoOrphanTask == handler = "done" => (pump = "done" /\ watch = "done")

\* A closed tab releases the stream, also when no tick ever comes.
DisconnectReleases == (client = "closed") ~> (gen = "closed" /\ handler = "done")

\* A stream that ends by itself also ends the handler.
PumpEndReleases == (pump = "done") ~> (gen = "closed" /\ handler = "done")

=============================================================================
