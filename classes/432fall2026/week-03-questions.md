---
id: w03-pool-01-averaging-versus-ridge
title: "Averaging Correlated Predictors versus Ridge"
---

Two highly correlated predictors are replaced by a single predictor formed by averaging the two columns of the design matrix row by row. Compare fitting a regression using this averaged predictor with retaining both original predictors and using ridge regression. Give two specific examples: one where averaging is reasonable and one where it causes a problem. Explain why in each case.

This question is contributed by jingyi64.

---
id: w03-pool-02-changing-coordinates
title: "Does Changing Coordinates Change Ridge?"
---

Two predictors $X_1$ and $X_2$ are centered and scaled to unit variance, and they are not perfectly correlated. Replace them by

$$
Z_1=\frac{X_1+X_2}{\sqrt{2}},
\qquad
Z_2=\frac{X_1-X_2}{\sqrt{2}}.
$$

Fit ridge using these new predictors with the same $\lambda$, without further standardization. Will the predictions change? Could the answer change if we standardize $Z_1$ and $Z_2$ to unit variance before fitting? Explain why in each case.

This question is contributed by chahak2 and shushim2.

---
id: w03-pool-03-shrinking-a-precise-coefficient
title: "Why Shrink a Coefficient That Is Already Estimated Precisely?"
---

One predictor is independent of the others and has a strong relationship with the response. Several other predictors are highly correlated with each other, making their individual coefficients unstable. Ridge applies the same penalty to all coefficients. Could a penalty that helps the unstable group hurt prediction by shrinking the strong predictor too much? Explain when the overall tradeoff would be worthwhile.

This question is contributed by nkalele2.

---
id: w03-pool-04-same-effective-degrees-of-freedom
title: "Does the Same Effective Degree of Freedom Mean the Same Prediction Performance?"
---

Consider two models fitted to the same data with two predictors. Both minimize

$$
\mathrm{Loss}+\lambda_1\beta_1^2+\lambda_2\beta_2^2,
$$

where $\mathrm{Loss}$ is the squared-error loss and the intercept is not penalized. The models use different choices of $\lambda_1$ and $\lambda_2$, so they shrink the two coefficients differently, but they have the same effective degrees of freedom. Should we expect the same prediction performance because the models are equally complex according to this measure? Explain your reasoning.

This question is contributed by amoghu2.

---
id: w03-pool-05-ridge-with-more-data
title: "Does Ridge Become More Useful When We Collect More Data?"
---

Ridge improves prediction over OLS with 50 training observations. We then collect 5,000 observations from the same population, keeping the number of predictors fixed. Should we expect ridge's advantage to become larger or smaller? Explain how increasing the sample size changes the bias-variance tradeoff and whether we should keep the same penalty.

This question is contributed by cudzich3 and lugong2.

---
id: w03-pool-06-penalizing-the-intercept
title: "What Changes When We Penalize the Intercept?"
---

Fit a ridge regression with an intercept. Now add 100 to every response and refit using the same predictors and the same $\lambda$. Should every prediction increase by exactly 100? Compare leaving the intercept unpenalized with including it in the ridge penalty. Explain why the two approaches can behave differently.

This question is contributed by connerj2, lb16, and ousher2.

---
id: w03-pool-07-standardization-before-cross-validation
title: "Can Predictor Standardization Leak Information into Cross-Validation?"
---

Before cross-validation, an analyst standardizes the predictors using means and standard deviations calculated from the entire dataset. The response is left unchanged. The analyst argues that there is no data leakage because standardization uses only the predictors, not the response. Is this reasoning correct? Explain how validation information could enter the fitted procedure and where standardization should take place.

This question is contributed by allyw2, bugatha2, and runting5.

---
id: w03-pool-08-cross-validation-uncertainty-one-se-rule
title: "Is the One-Standard-Error Rule Always a Safer Choice?"
---

A ridge cross-validation curve is nearly flat around its minimum, and the estimated standard error is large. The one-standard-error rule selects a much larger penalty than the penalty that minimizes CV error. Is this necessarily a safer choice for prediction? Explain what could go wrong and what information from cross-validation you would examine before choosing between the two penalties, without using the test data.

This question is contributed by owenp3.

---
id: w03-pool-09-early-stopping-as-regularization
title: "Can Early Stopping Act Like Ridge Regularization?"
---

Use gradient descent to fit least squares with no ridge penalty. Work with centered data, start the coefficients at zero, and use a small fixed step size. Instead of running the algorithm to convergence, stop after a limited number of iterations. Can early stopping act as regularization? Must the resulting fit be exactly the same as a ridge fit for some $\lambda$? Explain your reasoning.

This question is contributed by prerith2.

---
id: w03-pool-10-testing-an-explain-to-me-skill
title: "Does Your Explain-to-Me Skill Work as Intended?"
---

After creating an `explain-to-me` skill with AI, how would you test whether it actually explains homework questions in the way you intended?

This question is contributed by mp79.

---
id: w03-pool-11-skill-discovery-and-reloading
title: "Why Did a Skill Only Work After Reloading the Editor?"
---

A student saved an `explain-to-me` skill in the project's skill folder, but the agent reported "Unknown skill." Moving it to the user-level skill folder did not solve the problem. The skill only worked after reloading the editor. What could explain this, and how should the agent diagnose the problem?

This question is contributed by stewary2.
