---
id: w04-pool-01-zero-coefficient-importance
title: "Does a Zero Coefficient Mean the Predictor Is Unimportant?"
---

Two standardized predictors are identical. A lasso solution assigns coefficient $c>0$ to the first and zero to the second. Would splitting $c$ equally between them change the predictions or the lasso penalty? What does this imply about interpreting the zero coefficient? Would adding a ridge penalty distinguish the two allocations?

This question is contributed by kum2 and hx27.

---
id: w04-pool-02-selection-and-prediction-stability
title: "How Different Are Predictions When Lasso Selects Different Variables?"
---

Two centered predictors have unit variance and correlation $\rho$. Two fitted models use $cX_1$ and $cX_2$, respectively for prediction, for the same fixed coefficient $c$. What happens to the prediction as $\rho$ approaches one? Does agreement between the predictions establish that either model predicts $Y$ accurately?

This question is contributed by zm25, bugatha2, shushim2, st58, and tingyun3.

---
id: w04-pool-03-selection-frequency-importance
title: "Can an Irrelevant Predictor Be Selected More Often Than a Relevant One?"
---

A predictor with true coefficient zero is highly correlated with a strong signal predictor. Another predictor has a small nonzero coefficient. Could lasso select the zero-coefficient predictor more frequently across repeated samples? What would this tell us about using selection frequency as a ranking of variable importance?

This question is contributed by wenhao7.

---
id: w04-pool-04-smooth-penalty-sparsity
title: "Does Sparsity Disappear as Soon as the Penalty Becomes Smooth?"
---

Consider the one-variable objective

$$
\frac12(\beta-a)^2+\lambda|\beta|^q,
\qquad a\ne0,\quad \lambda>0,
$$

where $a$ is the unpenalized least-squares coefficient. Lasso uses $q=1$, while ridge uses $q=2$. If $1<q<2$, can the minimizer be exactly zero? Explain by examining the objective near zero rather than relying only on a picture of the constraint region.

This question is contributed by heta2 and willm4.

---
id: w04-pool-05-comparing-penalty-strengths
title: "Is the Same Numerical Penalty a Fair Comparison Between Lasso and Elastic Net?"
---

At the same numerical $\lambda$, elastic net retains both correlated signal predictors more often than lasso, but also selects more noise predictors. Does this establish an unavoidable cost of grouping correlated variables? Consider that the elastic-net absolute-value penalty has weight $\alpha\lambda$, where $0<\alpha<1$ is the mixing parameter. How could we make a more informative comparison, and what should cross-validation choose if prediction is the goal?

This question is contributed by owenp3 and bjass2.

---
id: w04-pool-06-cv-mean-and-uncertainty
title: "Are We Averaging Validation Errors or Measuring Their Uncertainty?"
---

Five-fold cross-validation produces five validation MSEs for each penalty. One student divides their sum by five; another divides their standard deviation by $\sqrt{5}$. Are these competing estimates of the same quantity? Explain how each enters the minimum-error or one-standard-error rule, and why using more folds does not automatically guarantee a better penalty choice.

This question is adapted from related questions contributed by lb16, sgs11, and cudzich3.

---
id: w04-pool-07-measurement-units-and-selection
title: "Can Changing Measurement Units Change Which Variables Lasso Selects?"
---

A predictor is converted from meters to centimeters. We refit lasso using the same numerical penalty without standardizing the predictors. Could the selected variables or predictions change, even though the underlying information is unchanged? Explain what standardization would change.

This question is adapted from related questions contributed by jingyi64 and melikah2.

---
id: w04-pool-08-test-set-model-selection
title: "Does Cross-Validation Protect Us If We Later Choose a Model Using the Test Set?"
---

An analyst considers several elastic-net mixing parameters. For each one, cross-validation on the training data selects the penalty. The analyst then evaluates every resulting model on the final test set and reports the smallest test MSE. Is this an honest evaluation because every penalty was chosen by cross-validation? Explain how the comparison should be performed.

This question is adapted from a related question contributed by nkalele2.

---
id: w04-pool-09-validation-for-future-prediction
title: "Is Random Cross-Validation Appropriate for Predicting the Future?"
---

We use weekly sales data to build a model for predicting future sales. An analyst randomly divides the observations into five folds, allowing later weeks to help predict earlier weeks. Does this validation scheme represent the intended prediction task? Describe a more appropriate split and explain what it tests.

This question is adapted from a related question contributed by ericc13.

---
id: w04-pool-10-coordinate-descent-zero-coefficients
title: "When Should a Zero Coefficient Become Nonzero?"
---

During coordinate descent for lasso, a coefficient is set to zero. After other coefficients are updated, could this coefficient need to become nonzero again? Explain how the partial residual changes and how the soft-thresholding update determines whether the coefficient stays zero.

This question is adapted from a related question contributed by prerith2.
