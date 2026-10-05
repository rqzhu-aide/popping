---
id: w06-pool-01-class-imbalance-cutoff
title: "Should class imbalance change the cutoff?"
---

The notes say that class imbalance alone does not change the Bayes cutoff of $1/2$. But in the logistic example only 29 percent of the cases are positive, and at cutoff $0.5$ the model catches only about 31 percent of them. Lowering the cutoff feels like the natural fix.

If imbalance is not a reason to move the cutoff, is lowering it really just a hidden way of saying that missing a positive costs more than a false alarm?

This question is contributed by cgao19.

---
id: w06-pool-02-roc-model-cutoff
title: "ROC Curves and Classification Cutoffs"
---

My logistic regression model has a slightly higher test AUC than KNN. If missing a malignant case is more costly than incorrectly flagging a benign case, could KNN still be preferable? How should I use the ROC curves to choose both the model and its classification cutoff?

This question is contributed by jingyi64.

---
id: w06-pool-03-cost-weighted-cutoff
title: "Cost-weighted cutoff: why 0.2 instead of 0.5?"
---

If a false negative costs four times as much as a false positive, why does the cost-minimizing rule classify an observation as positive when its estimated probability is above 0.2 instead of 0.5? And how would lowering the cutoff from 0.5 to 0.2 change sensitivity and specificity?

This question is contributed by nnigam2.

---
id: w06-pool-04-knn-sample-size-auc-gap
title: "Does More Data Close the 1-NN Training and Test AUC Gap?"
---

In Homework 6, 1-NN had a training AUC of 1.0000 and a lower test AUC. When each training observation is returned as its own nearest neighbor, its fitted probability equals its label. If we keep $k=1$ and increase the training sample size, must the test AUC approach 1? How does overlap between the classes affect the answer? Would allowing $k$ to grow with sample size change your reasoning?

This question is adapted from a related question contributed by chahak2.

---
id: w06-pool-05-feature-scaling-knn-logistic
title: "Why Does Predictor Scaling Affect KNN and Logistic Regression Differently?"
---

Suppose we center and scale predictors using training-set statistics. How can this change the nearest neighbors and predictions of KNN with Euclidean distance? Why can an unpenalized logistic regression with an intercept retain the same fitted probabilities after its coefficients are adjusted for the rescaling? Would this still hold with a penalty on the coefficients?

This question is adapted from a related question contributed by ruitong7.

---
id: w06-pool-06-bayes-error-estimation
title: "Bayes error, bias, and variance"
---

If we knew the true probability for every observation, why could we still make classification errors, and how would those errors differ from errors caused by estimating probabilities?

This question is contributed by brianc19.

---
id: w06-pool-07-auc-single-cutoff
title: "Why Lean on AUC If You Pick One Cutoff?"
---

A model can have a great AUC and still make bad calls at the cutoff you actually use, since AUC only scores ranking. If you only ever predict at one threshold, why do we lean on AUC so much instead of judging the model at that threshold directly?

This question is contributed by amoghu2.

---
id: w06-pool-08-same-roc-different-decisions
title: "Same AUC, Different Decisions"
---

Could two models have exactly the same ROC curve and AUC but make different predictions at a cutoff of 0.5? What would explain this?

This question is contributed by kum2.

---
id: w06-pool-09-crossing-roc-partial-auc
title: "Comparing models when ROC curves cross"
---

In my Homework 6 Question 1, the KNN ($k=45$) and logistic regression ROC curves cross: KNN is higher at false positive rates below about $0.05$, logistic catches up above that, and their AUCs differ by only $0.004$. If a cancer screen will only ever operate at low false positive rates, should we compare partial AUC over that range (or sensitivity at a fixed specificity) instead of total AUC, and how do we choose that range before looking at the test data?

This question is contributed by hx27.

---
id: w06-pool-10-ai-code-redundancy
title: "How Should We Handle Duplicated AI-Generated Analysis Code?"
---

During peer review, I found duplicated AI-generated code in Parts A and B of a homework report. How could we consolidate it without changing the analysis, and what results should we compare before and after the change?

This question is adapted from a related question contributed by ericc13.
