---
title: "Bootstrap Confidence Intervals"
author: "Arthas Menethil (amenet2)"
---

Explain how the bootstrap can be used to construct a 95% confidence
interval for the median. Consider the following R code:

```r
set.seed(123)
x <- rexp(100, rate = 0.5)
boot_medians <- replicate(1000, median(sample(x, replace = TRUE)))
ci <- quantile(boot_medians, c(0.025, 0.975))
```

What assumptions does the bootstrap make about the underlying distribution?
