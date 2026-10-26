---
id: w09-pool-01-linear-nonlinear-generalization
title: "Linear vs. Nonlinear Kernels"
---

Why might a linear SVM perform better than a more flexible nonlinear SVM, even when the nonlinear model can capture more complex patterns in the data?

This question is contributed by allyw2.

---
id: w09-pool-02-kernel-choice-collinearity
title: "Linear or Radial Kernel for Collinear Data?"
---

Suppose we have $p$ predictors and $n$ observations, with $p\gg n$, and many predictors are highly collinear. For predictor vectors $\mathbf x,\mathbf x'\in\mathbb R^p$, compare a linear kernel

$$
K(\mathbf x,\mathbf x')=\mathbf x^{\mathsf T}\mathbf x'
$$

with a radial kernel

$$
K(\mathbf x,\mathbf x')=\exp\{-\gamma\|\mathbf x-\mathbf x'\|^2\},
\qquad \gamma>0.
$$

What are the advantages and disadvantages of fitting an SVM with each kernel? In particular, how does collinearity affect each kernel's interpretability, risk of overfitting, and tuning?

This question is contributed by rahafaa2.

---
id: w09-pool-03-nearly-linear-radial
title: "If the Winning Radial Kernel Is Almost Linear, Is It Really Better?"
---

In one Homework 9 Question 1 run on the SAheart data, a radial SVM with $\gamma=0.01$ had the lowest cross-validation error, yet behaved almost identically to a linear SVM. With $\gamma=1$, the radial SVM predicted the majority class at every tested value of $C$.

When a flexible kernel wins only by imitating a linear one, and the error gap is within one standard error, should we still prefer it on predictive grounds, or does the bias-variance picture suggest that the linear SVM is the better-supported choice?

This question is contributed by hx27.

---
id: w09-pool-04-similar-cv-kernels
title: "When Kernel Performance Is Similar, Should We Choose the Linear Model?"
---

After tuning SVMs with linear, polynomial, and radial kernels using the same cross-validation folds, you find that they have similar predictive performance. How should you choose among them? Should you simply choose the linear SVM on the grounds that it is the simplest model? Explain your reasoning.

This question is adapted from related questions contributed by brianc19, bugatha2, sgs11, st58, and stewary2.

---
id: w09-pool-05-cost-gamma-bias-variance
title: "How Do Cost and Radial Scale Affect Bias and Variance?"
---

In a radial SVM, how does increasing or decreasing $C$ and $\gamma$ affect model flexibility, bias, and variance? Is grid search an appropriate way to select these parameters?

This question is contributed by lb16.

---
id: w09-pool-06-gamma-approaches-zero
title: "What Happens as Gamma Approaches Zero?"
---

For standardized predictor vectors $\mathbf x,\mathbf z\in\mathbb R^p$, consider the radial kernel

$$
K(\mathbf x,\mathbf z)=\exp\{-\gamma\|\mathbf x-\mathbf z\|^2\},
\qquad \gamma>0.
$$

All kernel values approach one as $\gamma\to0$. Holding $C$ fixed, does a very small $\gamma$ make an RBF SVM approximately linear, or can it simply make the classifier too strongly regularized? Explain your reasoning.

This question is adapted from a related question contributed by dehuili2.

---
id: w09-pool-07-tuning-grid
title: "How Should We Choose the Tuning Grid?"
---

When tuning $C$ and $\gamma$, how should we choose the range and spacing of candidate values? What difference does it make to use values spaced by factors of 10 rather than 100?

This question is contributed by lugong2.

---
id: w09-pool-08-valid-kernels
title: "Why Isn't Every Similarity Function a Valid Kernel?"
---

A kernel SVM replaces feature-space inner products with a kernel function $K(\mathbf x,\mathbf z)$. For training observations $\mathbf x_1,\ldots,\mathbf x_n\in\mathbb R^p$, the kernel matrix has entries $K_{ij}=K(\mathbf x_i,\mathbf x_j)$.

Why can't we use any similarity function as a kernel? How does requiring the kernel matrix to be positive semidefinite for every finite set of inputs connect to the existence of a feature map, and what can go wrong with the SVM optimization problem if that requirement fails?

This question is contributed by haorans9.

---
id: w09-pool-09-polynomial-feature-weights
title: "How Does a Polynomial Kernel Weight Linear and Interaction Terms?"
---

For predictor vectors $\mathbf x,\mathbf z\in\mathbb R^p$, with $p\geq2$, consider the degree-2 polynomial kernel

$$
K(\mathbf x,\mathbf z)
=\bigl(\operatorname{coef}_0+\gamma\mathbf x^{\mathsf T}\mathbf z\bigr)^2,
\qquad \operatorname{coef}_0\geq0,\quad\gamma>0.
$$

How can we control the relative influence of interaction terms, such as $x_1x_2$, and linear terms, such as $x_1$ and $x_2$? How do the kernel parameters affect the relative weighting of these terms?

This question is contributed by schuang7.
