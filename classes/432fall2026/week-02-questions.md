---
id: w02-pool-01-criteria-disagreement
title: "When AIC, BIC, and Cp Disagree"
---

Suppose we compare candidate linear regression models fitted to the same dataset. Under what conditions might AIC, BIC, and Mallows' $C_p$ select different models? Describe a situation in which the improvement in fit is enough to justify an additional predictor under one criterion but not another. Explain how the fit term, the complexity penalty, and the sample size enter that decision.

This question is contributed by brianc19, owenp3, and willm4.

---
id: w02-pool-02-when-to-prefer-bic
title: "Why BIC Often Selects a Smaller Model"
---

Why does BIC usually favor smaller linear regression models than AIC or Mallows' $C_p$? Under what modeling goals and assumptions would you prefer BIC? Explain why choosing the smaller model is not automatically the better decision, even when the candidate models have similar validation MSE.

This question is contributed by gjia3, hx27, and schuang7.

---
id: w02-pool-03-prediction-versus-explanation
title: "Prediction or Explanation: Two Real Decisions"
---

Give two specific real-life examples of using a regression model: one where accurate prediction is the main goal, and another where understanding the relationship between particular predictors and the response is the main goal. For each example, state the decision the analysis should support and explain how that goal affects which model you would prefer. Why might the model with the lowest prediction error be less useful for the second task?

This question is contributed by haorans9 and ousher2.

---
id: w02-pool-04-test-error-without-a-u-shape
title: "When Expected Test Error Is Not U-Shaped"
---

Consider a sequence of nested least-squares models that add predictors one at a time, with the training sample size and design held fixed as in the Week 2 lecture. Must expected test MSE have a U-shaped curve as the number of predictors increases? Give two specific examples in which the theoretical curve is not U-shaped over the range of candidate models being compared. Explain each example using approximation bias and estimation variance, rather than fluctuations from one simulation run.

This question is adapted from a related question contributed by stewary2.

---
id: w02-pool-05-one-target-versus-a-population
title: "Predicting for One Target or an Entire Population"
---

Give a real-life example in which the best model for predicting the response of someone or something with a particular set of characteristics could differ from the best model for minimizing average prediction error across the whole population. Describe the specific target, the population, and a predictor whose usefulness differs between the two tasks. Explain in words why including that predictor could help one task more than the other. Use no code or mathematical formulas.

This question is contributed by cudzich3, gaoxinc2, and mannat2.

---
id: w02-pool-06-best-subset-versus-stepwise
title: "Best Subset Selection Versus Stepwise Selection"
---

Compare best subset selection with stepwise selection when both use the same model-selection criterion. What are the advantages and disadvantages of each in terms of computation, the models searched, and dependence on earlier selection decisions? Describe a situation in which stepwise selection could miss a model found by best subset selection. Does finding the smallest criterion value among all subsets guarantee the lowest error on future data?

This question is contributed by chahak2 and ruitong7.

---
id: w02-pool-07-selection-inside-cross-validation
title: "Why Variable Selection Must Be Inside Cross-Validation"
---

An analyst uses the entire dataset to select predictors, then refits that selected model separately in each training fold of 10-fold cross-validation. The analyst argues that the resulting MSE is an honest estimate of future prediction error because every observation is held out once. What information has already leaked into the validation folds? Describe how to evaluate the full selection-and-fitting procedure correctly, including where predictor selection happens and when a separate final test set may be used.

This question is contributed by nnigam2, shushim2, and wenhao7.

---
id: w02-pool-08-observed-versus-expected-test-error
title: "When One Test Set Favors the Worse Model on Average"
---

Suppose adding a predictor increases expected test MSE, averaged over repeated training and independent test responses as in the Week 2 lecture. Could the observed test MSE nevertheless decrease on one particular training/test response pair? Explain what varies from one repetition to another and why a single observed improvement does not contradict the expected-error comparison.

This question is contributed by tingyun3.

---
id: w02-pool-09-irreducible-noise-and-intervals
title: "Why More Data Cannot Eliminate Prediction Uncertainty"
---

In a correctly specified Gaussian linear regression with positive error variance, consider prediction at a fixed set of predictor values. A student claims that with enough training data, a 95% prediction interval for one new response can become arbitrarily narrow, just like a 95% confidence interval for the mean response. Where does this reasoning break down? Explain which source of uncertainty can shrink as the training sample grows and which remains for the new response.

This question is contributed by yanxif2.

---
id: w02-pool-10-ai-data-path-selection
title: "An AI Agent Finds the Wrong Copy of the Data"
---

While helping with Homework 2, an AI agent writes code that searches several folders for `diabetes.csv` and loads the first copy it finds. Suppose the search succeeds, but it finds an older copy outside the intended course data folder. What should you ask the agent to check before trusting the analysis, and how should it revise the file-loading code so another student can tell which dataset will be used when running the repository on a different computer?

This question is contributed by jyan36.

---
id: w02-pool-11-ai-rendering-permission-error
title: "Should an AI Agent Rewrite Code After a PermissionError?"
---

An AI agent runs a Python Quarto document, but rendering stops with a `PermissionError` when Quarto tries to write a log file. The agent proposes changing the regression code. What evidence in the error message and traceback should it examine before editing anything? Describe the next diagnostic step you would ask it to take and how the result would help distinguish a problem with the statistical code from a problem with the output location or required access.

This question is contributed by ziqiz13.
