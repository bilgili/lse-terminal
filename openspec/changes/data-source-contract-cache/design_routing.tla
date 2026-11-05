---------------------------- MODULE design_routing ----------------------------
(* D2/D10 source routing. Does a symbol always go back to the source that
   listed it?  L = lse (options, flow, macro; key-gated).  M = a plugin
   registered after lse with options + macro, no flow (moomoo-like).
   DefaultRule: "configured" = amended D2 (first registered with cap AND
   configured); "registered" = first registered with cap, 409 if that one
   is not configured.
   UiRule: "capAtCall" = amended D10 (capParam evaluated per request);
   "pinned" = the listing response names its source and the page sends it
   back explicitly on symbol calls. React macro page never sends provider
   and caches its catalog for the SPA session (EconomicCalendar.tsx:257). *)
EXTENDS Naturals, Sequences

CONSTANTS DefaultRule, UiRule, TapeClick

Order == <<"L", "M">>
Prov  == {"L", "M"}
Caps  == [p \in Prov |-> IF p = "L" THEN {"options", "flow", "macro"}
                                    ELSE {"options", "macro"}]
None  == "none"

VARIABLES conf, active, optOrigin, optSrc, reactOrigin, bad
vars == <<conf, active, optOrigin, optSrc, reactOrigin, bad>>

Eligible(cap) ==
  {i \in 1..Len(Order) :
     cap \in Caps[Order[i]] /\ (DefaultRule = "registered" \/ conf[Order[i]])}

Resolve(cap, param) ==
  IF param = "dflt" THEN
    IF Eligible(cap) = {} THEN "e409"
    ELSE LET i == CHOOSE j \in Eligible(cap) : \A k \in Eligible(cap) : j <= k
             p == Order[i]
         IN IF conf[p] THEN p ELSE "e409"
  ELSE IF cap \notin Caps[param] THEN "e404"
  ELSE IF ~conf[param] THEN "e409"
  ELSE param

CapParam(cap) == IF cap \in Caps[active] THEN active ELSE "dflt"

Init == /\ conf \in [Prov -> BOOLEAN]
        /\ active \in Prov
        /\ optOrigin = None /\ optSrc = None
        /\ reactOrigin = None
        /\ bad = FALSE

ToggleKey(p) == /\ conf' = [conf EXCEPT ![p] = ~@]
                /\ UNCHANGED <<active, optOrigin, optSrc, reactOrigin, bad>>
Switch(a) == /\ active' = a
             /\ UNCHANGED <<conf, optOrigin, optSrc, reactOrigin, bad>>

\* Options rail lists underlyings; user picks one.
OptPickRail == LET param == CapParam("options")
                   r == Resolve("options", param)
               IN /\ r \in Prov
                  /\ optOrigin' = r
                  /\ optSrc' = IF UiRule = "pinned" THEN r ELSE param
                  /\ UNCHANGED <<conf, active, reactOrigin, bad>>
\* ALL tape (no underlying) uses capParam("flow"); a tape click jumps to the chain.
OptPickTape == LET param == CapParam("flow")
                   r == Resolve("flow", param)
               IN /\ r \in Prov
                  /\ optOrigin' = r
                  /\ optSrc' = IF UiRule = "pinned" THEN r ELSE param
                  /\ UNCHANGED <<conf, active, reactOrigin, bad>>

\* Chain (route cap options) and tape-with-underlying (route cap flow);
\* both send capParam("options") under the amended D10.
OptSymCall(routeCap) ==
  /\ optOrigin # None
  /\ LET param == IF UiRule = "pinned" THEN optSrc ELSE CapParam("options")
         r == Resolve(routeCap, param)
     IN bad' = (bad \/ (r \in Prov /\ r # optOrigin))
  /\ UNCHANGED <<conf, active, optOrigin, optSrc, reactOrigin>>

ReactList == /\ reactOrigin = None
             /\ LET r == Resolve("macro", "dflt") IN
                  /\ r \in Prov
                  /\ reactOrigin' = r
             /\ UNCHANGED <<conf, active, optOrigin, optSrc, bad>>
ReactSeries == /\ reactOrigin # None
               /\ LET r == Resolve("macro", "dflt")
                  IN bad' = (bad \/ (r \in Prov /\ r # reactOrigin))
               /\ UNCHANGED <<conf, active, optOrigin, optSrc, reactOrigin>>

Next == \/ \E p \in Prov : ToggleKey(p)
        \/ \E a \in Prov : Switch(a)
        \/ OptPickRail \/ (TapeClick /\ OptPickTape)
        \/ OptSymCall("options") \/ OptSymCall("flow")
        \/ ReactList \/ ReactSeries

Spec == Init /\ [][Next]_vars

NoCrossSource == ~bad
=============================================================================
