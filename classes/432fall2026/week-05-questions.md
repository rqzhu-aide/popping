---
id: w05-pool-02-correlation-and-response-direction
title: "When Does Correlation Help KNN?"
---

In one Homework 05 simulation, KNN had lower test error when the predictors were strongly correlated than when they were independent. Should we expect this whenever predictors are correlated? Consider instead two highly correlated predictors whose difference, $X_1-X_2$, is important for predicting $Y$. Explain why the direction that predicts the response matters when judging whether Euclidean distance will find useful neighbors.

This question is adapted from a related question contributed by owenp3.

---
id: w05-pool-04-observed-and-latent-dimension
title: "Why Can KNN Worsen When the Number of Measured Variables Stays Fixed?"
---

In Homework 05, one simulation kept the number of measured predictors at 100 while changing the dimension of the latent variables that generated them. Why can KNN become less accurate as the latent dimension increases even though the observed dimension does not change? Explain what this says about the meaning of a nearby observation.

This question is adapted from a related question contributed by stewary2.

---
id: w05-pool-05-shifted-digit-pixels
title: "Are Two Shifted Images Far Apart to KNN?"
---

Two images show the same handwritten digit, but one is shifted in a large set of pixels. Would it be possible that their raw pixel vectors have a large Euclidean distance even though a person sees the same digit? Can you suggest a representation or preprocessing step that could make distance better reflect the similarity we care about?

This question is adapted from a related question contributed by yanxif2.

---
id: w05-pool-06-distance-changes-neighbors
title: "Can the Distance Measure Change the Nearest Neighbor?"
---

For target $(0,0)$, consider training points $A=(1,0)$, $B=(0,1)$, and $C=(0.6,0.6)$. Which point is nearest under Euclidean distance, or under a carefully specified Manhattan distance?  Can you give a real life example with the same logic?

This question is adapted from a related question contributed by dehuili2.

---
id: w05-pool-07-knn-outliers
title: "Which Outlier Matters More to KNN?"
---

At a target in a dense predictor region, one training observation has unusual predictor values far from the target but an ordinary response, while another is nearby but has an extreme response. Which one is more likely to distort a KNN regression prediction based on the mean response of its $k$ nearest neighbors, and why?

This question is adapted from a related question contributed by zexuanj2.

---
id: w05-pool-10-repeated-sensors
title: "Can Repeated Measurements Help KNN?"
---

Suppose ten sensors measure the same response-relevant latent feature, with independent measurement errors, while one sensor measures a second, equally predictive latent feature. All eleven observed predictors are standardized before KNN. After standardization, does Euclidean distance give the first feature too much influence? Give an example under which this type of procedure can still be better than just measuring each feature once.

This question is adapted from a related question contributed by calebsg3.
