# Genetic Algorithm Tuning System for RH Tools

Evolves optimal parameters for 5 Red Hat fixed tools using **local evaluation only** (zero API calls).

## Overview

This GA system optimizes parameters for:

1. **Compression GA** - Token savings with semantic preservation
2. **Thompson Router GA** - Multi-armed bandit routing parameters
3. **Caching GA** - Cache TTL and thresholds
4. **Capability Matrix GA** - Scoring weights for model selection
5. **Dashboard GA** - Learning rate and exploration decay

## System Architecture

```
ga_tuner.py (main GA loop)
├── Population: 50 individuals
├── Generations: 25 (configurable)
├── Selection: Tournament (k=3)
├── Crossover: 0.8 probability
├── Mutation: 0.1 probability per gene
└── Evaluators (parallel fitness evaluation)
    ├── compression_evaluator.py
    ├── thompson_evaluator.py
    ├── caching_evaluator.py
    ├── matrix_evaluator.py
    └── dashboard_evaluator.py
```

## Installation

```bash
# Install dependencies
pip install numpy scipy scikit-learn pyyaml tiktoken

# Navigate to directory
cd ga_tuning
```

## Usage

### Run GA Tuning

```bash
# Run with default config
python ga_tuner.py

# Run with custom config
python ga_tuner.py --config config/ga_config.yaml
```

### Output Files

Results are saved to `./results/`:

- `ga_summary_{timestamp}.json` - Executive summary with best parameters
- `ga_best_parameters_{timestamp}.json` - Top 3 parameters per system
- `ga_fitness_history_{timestamp}.json` - Fitness scores per generation

Example output:

```json
{
  "compression": [
    {
      "fitness": 0.742,
      "parameters": {
        "compression_level": 2.3,
        "target_reduction": 0.35
      }
    }
  ],
  "thompson": [
    {
      "fitness": 0.685,
      "parameters": {
        "alpha_prior": 1.2,
        "beta_prior": 1.8,
        "cost_weight": 0.28
      }
    }
  ]
}
```

### Test Individual Evaluators

```bash
# Test compression evaluator
python evaluators/compression_evaluator.py

# Test Thompson router
python evaluators/thompson_evaluator.py

# Test caching
python evaluators/caching_evaluator.py

# Test capability matrix
python evaluators/matrix_evaluator.py

# Test dashboard learning
python evaluators/dashboard_evaluator.py
```

## Evaluator Details

### Compression Evaluator

- **Fitness**: token_savings × semantic_preservation
- **Test corpus**: memory files from `~/.claude/projects/[user]/memory/`
- **Constraint**: semantic_similarity > 0.85
- **Metrics**:
  - Token savings % (using tiktoken)
  - Cosine similarity (TF-IDF)
  - Compression effectiveness

### Thompson Router Evaluator

- **Fitness**: cost_savings × quality_maintained
- **Simulation**: 100 synthetic tasks with:
  - 5 task types (code_review, deployment, documentation, testing, debugging)
  - 4 models with realistic costs
  - Thompson sampling with exploration
- **Metrics**:
  - Routing accuracy vs. optimal model
  - Cost savings vs. baseline
  - Quality maintenance (target 0.85+)

### Caching Evaluator

- **Fitness**: cache_hit_rate × token_savings
- **Simulation**: Multi-turn conversations with:
  - Realistic content patterns (30 turns)
  - Cache invalidation modeling
  - Timeout simulation
- **Constraint**: false_negative_rate < 0.05
- **Metrics**:
  - Cache hit rate
  - Token savings
  - Invalidation reliability

### Capability Matrix Evaluator

- **Fitness**: routing_accuracy (target 92%+)
- **Test data**: 100+ memory files
- **Weights**: domain (0.1-0.5), complexity (0.2-0.6), task (0.1-0.5)
- **Metrics**:
  - Accuracy of model selection vs. optimal
  - Complexity-based alignment
  - Task type matching

### Dashboard Evaluator

- **Fitness**: learning_speed × stability
- **Simulation**: Thompson learning over 500 synthetic tasks with:
  - Concept drift (quality changes over time)
  - Exploration/exploitation tradeoff
  - Quality regression detection
- **Targets**:
  - Learning speed: 20%+ improvement
  - Quality regression: < 5%
- **Metrics**:
  - Final accuracy
  - Learning curve shape
  - Stability under drift

## Configuration

Edit `config/ga_config.yaml`:

```yaml
population_size: 50      # Higher = more thorough but slower
generations: 25          # More generations = better convergence
crossover_rate: 0.8      # Higher = more mixing
mutation_rate: 0.1       # Higher = more exploration
tournament_k: 3          # Tournament size for selection
```

## Performance

- **Runtime**: ~2-3 minutes (25 generations, 50 population)
- **Cost**: Zero (local evaluation only)
- **Evaluations**: 1,250 total (50 × 25)

### Typical Fitness Scores

- Compression: 0.6-0.75 (token savings × semantic preservation)
- Thompson: 0.60-0.80 (cost-quality tradeoff)
- Caching: 0.55-0.75 (hit rate × savings)
- Matrix: 0.75-0.95 (routing accuracy)
- Dashboard: 0.65-0.85 (learning speed × stability)

## Constraints & Validation

All evaluators enforce constraints:

| System | Constraint | Penalty |
|--------|-----------|---------|
| Compression | semantic_similarity > 0.85 | Hard constraint |
| Thompson | quality >= 0.85 | Scoring penalty |
| Caching | false_negative_rate < 0.05 | Exponential penalty |
| Matrix | routing_accuracy target 92% | Score bounded to accuracy |
| Dashboard | quality_regression < 5% | Stability penalty |

## Integration with RH Tools

### Apply Best Parameters

```python
# Load best parameters
import json

with open('results/ga_best_parameters_20260925_153000.json') as f:
    best_params = json.load(f)

# Apply to compression
compression_params = best_params['compression'][0]['parameters']
# compression_level = 2.3, target_reduction = 0.35

# Apply to Thompson router
thompson_params = best_params['thompson'][0]['parameters']
# alpha_prior = 1.2, beta_prior = 1.8, cost_weight = 0.28

# Apply to cache control
caching_params = best_params['caching'][0]['parameters']
# ttl_seconds = 350, cache_threshold = 0.25

# Apply to capability matrix
matrix_params = best_params['matrix'][0]['parameters']
# domain_weight = 0.3, complexity_weight = 0.45, task_weight = 0.25

# Apply to dashboard
dashboard_params = best_params['dashboard'][0]['parameters']
# learning_rate = 0.08, exploration_decay = 0.92, alert_threshold = 0.6
```

## Testing Strategy

### Validate on Real Data

1. **Compression**: Test on actual memory files
2. **Thompson**: Validate with real task logs from disseminator
3. **Caching**: Test on multi-turn conversation traces
4. **Matrix**: Route real RH files and measure accuracy
5. **Dashboard**: Simulate with historical outcomes

### A/B Testing

Once tuned, run A/B tests:

```
Control: Current parameters
Treatment: GA-optimized parameters

Measure: Cost, quality, routing accuracy
Duration: 2-4 weeks of production traffic
```

## Troubleshooting

### "No test files found in RH memory directory"

Set `CE_MEMORY_DIR` to the intended memory corpus and check that memory files exist:

```bash
ls -la "${CE_MEMORY_DIR:-$HOME/.claude/memory}"
```

### Low fitness scores

- May indicate no RH files available (using synthetic data)
- Adjust parameter ranges in config
- Increase generations for better convergence

### Memory errors

- Reduce population_size in config
- Reduce number of generations
- Run on machine with more RAM

## Future Enhancements

- Multi-objective optimization (Pareto frontier)
- Parallel fitness evaluation (ProcessPoolExecutor)
- Parameter correlation analysis
- Sensitivity analysis per parameter
- Dynamic constraint adjustment
- Integration with live RH telemetry

## References

- Genetic Algorithms: https://en.wikipedia.org/wiki/Genetic_algorithm
- Thompson Sampling: https://en.wikipedia.org/wiki/Thompson_sampling
- Parameter Optimization: https://en.wikipedia.org/wiki/Hyperparameter_optimization
