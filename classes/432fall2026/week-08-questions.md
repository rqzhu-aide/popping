---
id: w08-pool-01-margin-scaling
title: "Can Scaling the Coefficients Enlarge the Margin?"
---

For linearly separable data with class labels $y_i\in\{-1,1\}$, let $f(\mathbf x)=\beta_0+\mathbf x^{\mathsf T}\boldsymbol\beta$, where $\mathbf x,\boldsymbol\beta\in\mathbb R^p$. Suppose we replace both $\beta_0$ and $\boldsymbol\beta$ by $c\beta_0$ and $c\boldsymbol\beta$, where $c>1$. Does this create a larger gap between the two classes or a better maximum-margin solution? Explain what happens to the decision boundary, the functional margins $y_i f(\mathbf x_i)$, and the geometric margin.

This question is adapted from a related question contributed by haorans9.

---
id: w08-pool-02-lagrangian-constraints
title: "How Does the Lagrangian Enforce Constraints?"
---

For a hard-margin linear SVM with observations $\mathbf x_i\in\mathbb R^p$, labels $y_i\in\{-1,1\}$, and score $f(\mathbf x)=\beta_0+\mathbf x^{\mathsf T}\boldsymbol\beta$, the Lagrangian is

$$
\mathcal L(\beta_0,\boldsymbol\beta,\boldsymbol\alpha)
=\frac12\|\boldsymbol\beta\|_2^2
+\sum_{i=1}^n\alpha_i\{1-y_i f(\mathbf x_i)\},
\qquad \alpha_i\geq0.
$$

Why can the constrained SVM problem be written as minimizing over $\beta_0$ and $\boldsymbol\beta$ after maximizing this Lagrangian over the nonnegative multipliers? Explain what the maximization does when a margin constraint is satisfied and when it is violated. What problem results when the order of minimization and maximization is reversed?

This question is adapted from a related question contributed by schuang7.

---
id: w08-pool-03-nonsupport-boundary
title: "When Does a Point Start Affecting the Fit?"
---

For a fitted hard-margin linear SVM, move one non-support observation toward the decision boundary while holding all other observations fixed and refitting the model as the point moves. Can the optimal boundary change while this point remains strictly outside the margin? Is reaching the margin enough to guarantee a change? Explain using the SVM constraints and the observation's dual coefficient $\alpha_i$.

This question is adapted from a related question contributed by wenhao7.

---
id: w08-pool-04-primal-dual-computation
title: "When Is the Dual Easier to Solve?"
---

Why can the dual formulation of a linear SVM be easier to solve than the primal formulation? Give a concrete example and explain which features of the problem make the dual computationally advantageous.

This question is contributed by the instructor.

---
id: w08-pool-05-large-cost-slack
title: "Can a Very Large Cost Eliminate Slack?"
---

In a soft-margin linear SVM, $C>0$ controls the penalty on the nonnegative slack variables $\xi_i$. Suppose we keep increasing $C$ and find an optimal solution each time. Can we eventually make every slack variable arbitrarily close to zero, so that almost all training observations lie on the correct side of the decision boundary? Explain whether this is always possible.

This question is adapted from a related question contributed by dehuili2.

---
id: w08-pool-06-predictor-scaling
title: "Can Predictor Scaling Change the Support Vectors?"
---

For linearly separable data, how does changing the scale of one predictor affect a hard-margin linear SVM? Can it change the fitted decision boundary or which observations are support vectors? Explain using a two-dimensional graphical demonstration.

This question is adapted from a related question contributed by zm25.

---
id: w08-pool-07-equal-accuracy-support-vectors
title: "Can Equal Accuracy Hide Different Support Vectors?"
---

Two soft-margin linear SVMs are fitted to the same training data using different values of $C$. Both classify every training observation correctly. Could one still have more support vectors?

This question is adapted from related questions contributed by ziqiz13 and kum2.

---
id: w08-pool-08-soft-margin-separable-data
title: "Does Separable Data Require the Hard-Margin Solution?"
---

If the training classes are linearly separable, must a soft-margin SVM recover the hard-margin solution? How does the answer depend on $C$, and why might it tolerate violations even though perfect separation is possible?

This question is adapted from related questions contributed by rruiz35 and zexuanj2.

---
id: w08-pool-09-removing-support-vector
title: "Does Removing a Support Vector Change the Boundary?"
---

Does deleting a support vector necessarily change the maximum-margin boundary? Construct a two-dimensional example where other observations keep the boundary unchanged.

This question is adapted from a related question contributed by cgao19.

---
id: w08-pool-10-svm-logistic-loss
title: "Why Do SVM and Logistic Regression Use Observations Differently?"
---

An observation strictly outside the SVM margin can have no effect on the fitted boundary, while it can still affect logistic regression. How do the two loss functions explain this difference?

This question is adapted from related questions contributed by nnigam2 and prerith2.

---
id: w08-pool-11-cost-bias-variance
title: "How Does Cost Affect Bias and Variance?"
---

Imagine repeatedly drawing training samples of the same size from the same population and fitting a soft-margin linear SVM. How would you expect the fitted boundary to vary across samples under a small versus a large $C$? Why does a smaller $C$ correspond to stronger regularization, and how does this affect the bias-variance trade-off?

This question is adapted from related questions contributed by yanxif2, stewary2, and amoghu2.

---
id: w08-pool-12-skill-specificity-flexibility
title: "How Much Flexibility Should an Analysis Skill Allow?"
---

Two AI agents follow the same statistical analysis skill on the same dataset but choose different sets of candidate models. Does this indicate useful flexibility or insufficient guidance? Which analysis decisions should a reusable skill fix in advance, and which should it allow the agent to adapt to the dataset? Give one concrete instruction that strikes this balance without depending on a particular dataset.

This question is adapted from related questions contributed by chahak2, bugatha2, panico2, and jyan36.

---
id: w08-pool-13-skill-reproducibility-quality
title: "Is Successful Execution Enough for a Good Skill?"
---

Two people run the same analysis skill on the same dataset but report different results. Is setting the same random seed enough to make their analyses reproducible? What other choices need to be specified or recorded? If two skills both accomplish their stated goal, what criteria would you use to judge which is better?

This question is adapted from related questions contributed by lugong2, maeved2, and lb16.
