---
id: w10-pool-01-averaging-and-bias
title: "Does Averaging Remove Bias?"
---

Why can averaging many unstable regression trees reduce prediction variance? If the individual trees systematically underpredict the response in some region, would averaging more trees eliminate that bias?

This question is adapted from a related question contributed by kyang53.

---
id: w10-pool-02-tree-diversity-and-signal
title: "Can Less Correlated Trees Produce a Worse Forest?"
---

Suppose only 2 of 100 predictors contain useful information. Reducing `mtry` may make the trees less correlated, but it also makes useful predictors less likely to be available at each split. When could this change increase the forest's prediction error? Explain the competing effects on bias and variance.

This question is adapted from related questions contributed by nnigam2 and shushim2.

---
id: w10-pool-03-more-trees-and-limits
title: "Why Can Adding More Trees Stop Helping?"
---

Why can adding more trees to a random forest initially improve prediction, yet eventually provide little further benefit? What limits the improvement from averaging, and how might changing the way individual trees are grown address those limits?

This question is adapted from a related question contributed by heta2.

---
id: w10-pool-04-node-size-and-mtry
title: "Do Node Size and mtry Control the Same Trade-off?"
---

How do increasing terminal-node size and increasing `mtry` affect a random forest's bias and variance? Do they act through the same mechanism? Could changing one reliably compensate for changing the other?

This question is adapted from a related question contributed by tgeorg23.

---
id: w10-pool-05-oob-fewer-trees
title: "Does Using Fewer Trees Make OOB Error Pessimistic?"
---

With bootstrap samples that draw $n$ times from $n$ training observations, each observation is excluded from roughly 37% of the trees. Its OOB prediction therefore uses fewer trees than the full forest uses for a new observation. What impact does using fewer trees have on OOB error and the stability of its estimate? Does this necessarily make OOB error larger than the forest's true test error? How might the total number of trees affect this difference?

This question is adapted from a related question contributed by chahak2.

---
id: w10-pool-06-oob-repeated-observations
title: "When Does Leaving Out One Observation Fail to Provide a Meaningful Test?"
---

Suppose a dataset contains several observations from each person, and our goal is to predict outcomes for entirely new people. An observation may be OOB for a tree even though other observations from the same person were used to train it. Would ordinary OOB error reliably estimate performance on new people? How should the sampling or evaluation procedure change?

This question is adapted from a related question contributed by sgs11.

---
id: w10-pool-07-oob-stability-and-uncertainty
title: "Does Stable OOB Error Mean a Precise Error Estimate?"
---

Suppose we fit the forest repeatedly with different random seeds and obtain almost identical OOB errors. Does this mean the estimated prediction error is very precise? What types of uncertainty are controlled in this process, and what types of uncertainty remain?

This question is adapted from a related question contributed by schuang7.

---
id: w10-pool-08-tuning-computational-budget
title: "Should We Spend More Computation on Tuning or on Stabilizing the Comparison?"
---

We have a limited computational budget. A small tuning grid produces several promising settings, but changing the random seed sometimes changes which setting has the lowest OOB error. Should we spend the remaining budget on more trees, repeated fits, or a larger tuning grid? What evidence would help us decide?

This question is adapted from related questions contributed by jyan36 and rahafaa2.

---
id: w10-pool-09-correlated-important-predictors
title: "How Does a Random Forest Handle Correlated Important Predictors?"
---

How does a random forest deal with several highly correlated predictors that are useful for predicting the response? How might different trees choose among these predictors, and what could this mean for the importance assigned to each variable?

This question is adapted from related questions contributed by calebsg3 and runting5.

---
id: w10-pool-10-adaptive-neighborhoods
title: "Why Can a Forest's Notion of Nearby Be Better Than Ordinary Distance?"
---

A random forest can be viewed as predicting from an adaptive neighborhood of training observations. How might this neighborhood differ from the one used by Euclidean KNN when many predictors are irrelevant? When could learning the neighborhood from the data become a weakness?

This question is adapted from related questions contributed by amoghu2 and prerith2.

---
id: w10-pool-11-predictive-importance-and-causality
title: "Does Predictive Importance Imply a Causal Effect?"
---

A random forest predicts $Y$ accurately and assigns high importance to predictor $X$. Does this establish that changing $X$ would change $Y$? What additional information or assumptions would be needed to investigate that causal question?

This question is adapted from a related question contributed by melikah2.
