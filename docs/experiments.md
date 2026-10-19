# CE experiment backlog

This is a parking catalog, not an approved roadmap. The entries below are exploratory proposals, not commitments or implementation requirements. Closed source issues were intentionally de-queued because the titles alone do not justify keeping dozens of independent open issues.

## Promotion criteria

Reopen or create a focused implementation issue only when it has:
- a concrete hypothesis and reason it matters to the current CE architecture;
- a baseline and measurable success/failure criteria;
- bounded scope, prerequisites, and an owner or explicit next action;
- cost, latency, privacy, operational, and regression considerations;
- a clear distinction from already implemented capabilities.

Do not add a model family, local inference dependency, or neural subsystem merely because it is listed here. Prefer the smallest testable experiment and keep normal CE operation independent of exploratory work.

## Catalog

Catalogued proposals: 92.

### Learning signals, representation, and memory

- [#176: outcome feedback as a portable learning signal](https://github.com/FlossWare/claude-ensemble/issues/176)
- [#177: semantic experience retrieval for learning](https://github.com/FlossWare/claude-ensemble/issues/177)
- [#179: context selection from learned experience](https://github.com/FlossWare/claude-ensemble/issues/179)
- [#184: learned experience representations](https://github.com/FlossWare/claude-ensemble/issues/184)
- [#186: neural outcome prediction](https://github.com/FlossWare/claude-ensemble/issues/186)
- [#190: continual neural learning and memory consolidation](https://github.com/FlossWare/claude-ensemble/issues/190)
- [#191: graph neural networks for experience relationships](https://github.com/FlossWare/claude-ensemble/issues/191)
- [#201: neural knowledge distillation](https://github.com/FlossWare/claude-ensemble/issues/201)
- [#208: synthetic experience generation](https://github.com/FlossWare/claude-ensemble/issues/208)
- [#210: neural compression of experience memory](https://github.com/FlossWare/claude-ensemble/issues/210)
- [#222: case-based reasoning from prior CE experience](https://github.com/FlossWare/claude-ensemble/issues/222)
- [#237: minimum-description learning for CE experiences](https://github.com/FlossWare/claude-ensemble/issues/237)
- [#242: cognitive memory models for CE](https://github.com/FlossWare/claude-ensemble/issues/242)
- [#258: experience forgetting and retention policies](https://github.com/FlossWare/claude-ensemble/issues/258)
- [#259: experience conflict detection and resolution](https://github.com/FlossWare/claude-ensemble/issues/259)
- [#260: knowledge and experience fusion](https://github.com/FlossWare/claude-ensemble/issues/260)

### Routing, orchestration, and multi-agent coordination

- [#178: learned worker routing](https://github.com/FlossWare/claude-ensemble/issues/178)
- [#181: ensemble learning across workers](https://github.com/FlossWare/claude-ensemble/issues/181)
- [#182: evolutionary orchestration strategies](https://github.com/FlossWare/claude-ensemble/issues/182)
- [#188: contextual bandit routing](https://github.com/FlossWare/claude-ensemble/issues/188)
- [#192: neuroevolution of orchestration strategies](https://github.com/FlossWare/claude-ensemble/issues/192)
- [#193: meta-learning for orchestration adaptation](https://github.com/FlossWare/claude-ensemble/issues/193)
- [#195: learned stopping decisions](https://github.com/FlossWare/claude-ensemble/issues/195)
- [#196: neural credit assignment](https://github.com/FlossWare/claude-ensemble/issues/196)
- [#197: counterfactual orchestration learning](https://github.com/FlossWare/claude-ensemble/issues/197)
- [#202: novelty detection for orchestration](https://github.com/FlossWare/claude-ensemble/issues/202)
- [#206: neural causal inference for orchestration](https://github.com/FlossWare/claude-ensemble/issues/206)
- [#207: neural world model for orchestration simulation](https://github.com/FlossWare/claude-ensemble/issues/207)
- [#212: hypernetworks for task-specific adaptation](https://github.com/FlossWare/claude-ensemble/issues/212)
- [#214: neural-symbolic orchestration](https://github.com/FlossWare/claude-ensemble/issues/214)
- [#216: Bayesian optimization for CE strategy tuning](https://github.com/FlossWare/claude-ensemble/issues/216)
- [#218: classical planning for orchestration](https://github.com/FlossWare/claude-ensemble/issues/218)
- [#219: Monte Carlo Tree Search for orchestration](https://github.com/FlossWare/claude-ensemble/issues/219)
- [#221: multi-objective optimization for CE orchestration](https://github.com/FlossWare/claude-ensemble/issues/221)
- [#223: constraint optimization for orchestration](https://github.com/FlossWare/claude-ensemble/issues/223)
- [#227: evolutionary strategy optimization for CE](https://github.com/FlossWare/claude-ensemble/issues/227)
- [#236: factorial analysis of orchestration factors](https://github.com/FlossWare/claude-ensemble/issues/236)
- [#239: market-based worker selection](https://github.com/FlossWare/claude-ensemble/issues/239)
- [#240: reputation learning for CE workers](https://github.com/FlossWare/claude-ensemble/issues/240)
- [#241: multi-agent negotiation for orchestration](https://github.com/FlossWare/claude-ensemble/issues/241)
- [#244: global-workspace-style coordination](https://github.com/FlossWare/claude-ensemble/issues/244)
- [#245: swarm intelligence for orchestration](https://github.com/FlossWare/claude-ensemble/issues/245)
- [#246: A* and heuristic search for orchestration](https://github.com/FlossWare/claude-ensemble/issues/246)
- [#247: branch-and-bound orchestration search](https://github.com/FlossWare/claude-ensemble/issues/247)
- [#248: A* and heuristic search for orchestration](https://github.com/FlossWare/claude-ensemble/issues/248)
- [#249: branch-and-bound orchestration search](https://github.com/FlossWare/claude-ensemble/issues/249)
- [#251: model checking for orchestration policies](https://github.com/FlossWare/claude-ensemble/issues/251)
- [#254: strategy promotion and retirement](https://github.com/FlossWare/claude-ensemble/issues/254)
- [#272: genetic programming for CE orchestration policies](https://github.com/FlossWare/claude-ensemble/issues/272)
- [#273: coevolution for CE strategy and workload adaptation](https://github.com/FlossWare/claude-ensemble/issues/273)

### Neural and predictive approaches

- [#185: neural similarity and metric learning](https://github.com/FlossWare/claude-ensemble/issues/185)
- [#187: neural uncertainty and deep ensembles](https://github.com/FlossWare/claude-ensemble/issues/187)
- [#194: neural attention and context selection](https://github.com/FlossWare/claude-ensemble/issues/194)
- [#198: neural active learning](https://github.com/FlossWare/claude-ensemble/issues/198)
- [#200: neural transfer learning across task families](https://github.com/FlossWare/claude-ensemble/issues/200)
- [#203: neural drift detection](https://github.com/FlossWare/claude-ensemble/issues/203)
- [#204: self-supervised learning from CE executions](https://github.com/FlossWare/claude-ensemble/issues/204)
- [#205: neural anomaly detection for executions](https://github.com/FlossWare/claude-ensemble/issues/205)
- [#211: neural architecture search for CE learners](https://github.com/FlossWare/claude-ensemble/issues/211)
- [#215: gradient-boosted models for CE prediction](https://github.com/FlossWare/claude-ensemble/issues/215)
- [#220: Gaussian-process uncertainty for CE prediction](https://github.com/FlossWare/claude-ensemble/issues/220)
- [#234: verified neural-symbolic execution](https://github.com/FlossWare/claude-ensemble/issues/234)

### Optimization and search

- [#213: learned optimization strategies](https://github.com/FlossWare/claude-ensemble/issues/213)
- [#267: multi-objective evolutionary optimization for CE](https://github.com/FlossWare/claude-ensemble/issues/267)
- [#268: adaptive mutation and crossover for CE evolution](https://github.com/FlossWare/claude-ensemble/issues/268)
- [#269: diversity preservation and niching for CE evolution](https://github.com/FlossWare/claude-ensemble/issues/269)
- [#270: island-model evolution for CE strategies](https://github.com/FlossWare/claude-ensemble/issues/270)
- [#271: memetic optimization for CE strategies](https://github.com/FlossWare/claude-ensemble/issues/271)

### Evaluation, experimentation, and decision quality

- [#180: confidence calibration](https://github.com/FlossWare/claude-ensemble/issues/180)
- [#189: learned value functions](https://github.com/FlossWare/claude-ensemble/issues/189)
- [#199: curriculum learning for CE tasks](https://github.com/FlossWare/claude-ensemble/issues/199)
- [#209: adversarial and self-challenge learning](https://github.com/FlossWare/claude-ensemble/issues/209)
- [#217: clustering and task-family discovery](https://github.com/FlossWare/claude-ensemble/issues/217)
- [#224: test-time compute and candidate selection](https://github.com/FlossWare/claude-ensemble/issues/224)
- [#225: retrieval and reranking strategies](https://github.com/FlossWare/claude-ensemble/issues/225)
- [#226: probabilistic graphical models for CE decisions](https://github.com/FlossWare/claude-ensemble/issues/226)
- [#228: information gain for CE decisions](https://github.com/FlossWare/claude-ensemble/issues/228)
- [#229: value of information for additional model calls](https://github.com/FlossWare/claude-ensemble/issues/229)
- [#230: risk-sensitive decision making](https://github.com/FlossWare/claude-ensemble/issues/230)
- [#231: automated experimental design for CE learning](https://github.com/FlossWare/claude-ensemble/issues/231)
- [#232: self-directed learning loop](https://github.com/FlossWare/claude-ensemble/issues/232)
- [#233: formal verification of learned decisions](https://github.com/FlossWare/claude-ensemble/issues/233)
- [#235: A/B and sequential experimentation for CE](https://github.com/FlossWare/claude-ensemble/issues/235)
- [#238: feature selection and prototype selection](https://github.com/FlossWare/claude-ensemble/issues/238)
- [#243: metacognitive self-evaluation](https://github.com/FlossWare/claude-ensemble/issues/243)
- [#250: Monte Carlo experiment selection](https://github.com/FlossWare/claude-ensemble/issues/250)
- [#252: temporal logic for CE policies](https://github.com/FlossWare/claude-ensemble/issues/252)
- [#253: automatic regression detection for learned strategies](https://github.com/FlossWare/claude-ensemble/issues/253)
- [#255: hypothesis generation from CE failures](https://github.com/FlossWare/claude-ensemble/issues/255)
- [#256: learned experiment prioritization](https://github.com/FlossWare/claude-ensemble/issues/256)
- [#257: active intervention selection for causal learning](https://github.com/FlossWare/claude-ensemble/issues/257)
- [#261: decision-theoretic utility modeling](https://github.com/FlossWare/claude-ensemble/issues/261)
