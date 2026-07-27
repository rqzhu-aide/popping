---
id: why-ensemble
title: "Why Ensemble?"
---

A single decision tree has high variance: small changes in the training data can produce a very different tree. Discuss with your team:

1. Why does averaging many trees reduce variance?
2. Does averaging also reduce bias? Why or why not?
3. What would happen to a random forest if the individual trees were highly correlated with each other?

---
id: tuning-gradient-boosting
title: "Tuning Gradient Boosting"
---

Gradient boosting builds trees sequentially, each correcting the errors of the previous ones. Discuss with your team:

1. What happens if the learning rate is set too high? Too low?
2. How does the number of trees interact with the learning rate?
3. Why might gradient boosting overfit where a random forest would not?
