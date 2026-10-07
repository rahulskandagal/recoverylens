"""Advanced analysis services layered on top of the recovery engine.

Every service works from persisted analysis records plus READ-ONLY reads of the evidence copy.
None of them writes to evidence, executes recovered content, or invents bytes.

  context            case loader + read-only evidence reader
  fragment_dna       per-fragment forensic fingerprint + similarity engine
  structure          plugin-based file-structure parsers (Universal File Structure Explorer)
  twin               Recovery Digital Twin (reconstruction history graph)
  possibility        Recovery Possibility Map
  simulator          Counterfactual Recovery Simulator (temporary copies only)
  cross_artifact     Cross-Artifact Intelligence (NetworkX)
  guardian           Recovery Safety Guardian (static analysis, isolation)
  confidence         Confidence profile + calibration engine
  benchmark          Recovery Benchmark Lab (synthetic ground truth)
  investigator       AI Recovery Investigator (evidence-grounded planner, approval-gated)
"""
