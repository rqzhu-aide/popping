---
id: w05-pool-01-duplicate-predictor-distance
title: "Can a Duplicated Predictor Change KNN's Neighbors?"
---

A KNN model uses Euclidean distance on two standardized predictors, $X_1$ and $X_2$. Replace $X_1$ with $r$ identical copies, keeping $X_2$, and standardize every column again. Can the nearest neighbors change even though the copies add no information? Write the resulting squared distance and explain how to weight the copies to recover the original distance.

This question is adapted from a related question contributed by zm25.

---
id: w05-pool-02-correlation-and-response-direction
title: "When Does Correlation Help KNN?"
---

In one Homework 05 simulation, KNN had lower test error when the predictors were strongly correlated than when they were independent. Should we expect this whenever predictors are correlated? Consider instead two highly correlated predictors whose difference, $X_1-X_2$, is important for predicting $Y$. Explain why the direction that predicts the response matters when judging whether Euclidean distance will find useful neighbors.

This question is adapted from a related question contributed by owenp3.

---
id: w05-pool-03-lasso-screening-nonlinear-signal
title: "Could Lasso Screening Discard a Predictor KNN Needs?"
---

Suppose $X_1$ is symmetric about zero and $Y=X_1^2+\varepsilon$, where $\varepsilon$ is independent mean-zero noise and several other predictors are noise. We use a lasso model with only linear terms to select predictors, then fit KNN on the selected columns. Could this procedure discard $X_1$ even though it helps predict $Y$? What does this example show about removing apparently irrelevant predictors before KNN?

This question is adapted from a related question contributed by brianc19.

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
id: w05-pool-08-categorical-distance
title: "How Should Neighborhood Categories Enter KNN Distance?"
---

Suppose a KNN model predicts house prices from standardized floor area and neighborhood, encoded as categories 1, 2, and 3. Euclidean distance treats neighborhoods 1 and 3 as farther apart than neighborhoods 1 and 2. When is that ordering meaningful, and how could a different encoding change which houses are nearest neighbors?

This question is adapted from a related question contributed by melikah2.

---
id: w05-pool-09-diagnosing-added-predictors
title: "Why Did Adding Predictors Make KNN Worse?"
---

Suppose a KNN model's cross-validation error rises after five standardized predictors are added. How would you determine whether the main cause is irrelevant noise, redundant measurements, or a poor distance representation, and what change would each diagnosis suggest?

This question is adapted from a related question contributed by selsa3.

---
id: w05-pool-10-repeated-sensors
title: "Can Repeated Measurements Help KNN?"
---

Suppose ten sensors measure the same response-relevant latent feature, with independent measurement errors, while one sensor measures a second, equally predictive latent feature. All eleven observed predictors are standardized before KNN. After standardization, does Euclidean distance give the first feature too much influence? Give an example under which this type of procedure can still be better than just measuring each feature once.

This question is adapted from a related question contributed by calebsg3.

---
id: w05-pool-11-package-switch-defaults
title: "Can Switching KNN Packages Change the Prediction?"
---

After a package error, an AI agent replaces `kknn::kknn(y ~ ., train = train, test = test, k = 3)` with `FNN::knn.reg(train = X, test = X0, y = y, k = 3)`. Here `X` and `X0` contain the same training and test predictors as `train` and `test`. The agent claims the fits are equivalent because both use Euclidean 3NN. With all other arguments at their defaults, what two package differences could change the predictions? Which difference can change the nearest neighbors, and which can change a prediction even when the same three neighbors are selected? How would you make the two fits comparable?

This question is adapted from a related question contributed by jyan36.
