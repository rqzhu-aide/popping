---
id: w07-pool-01-lda-qda-covariance-tradeoff
title: "When Does Pooling Help LDA?"
---

In the Homework 7 simulation, the class covariance matrices become more different as $\rho$ moves away from zero. At small positive $\rho$, LDA can have lower test error than QDA even though its shared-covariance assumption is false. Why can pooling help here, and how would increasing the training sample size change the comparison?

This question is adapted from a related question contributed by chahak2.

---
id: w07-pool-02-covariance-only-classification
title: "Can Covariance Alone Distinguish Two Classes?"
---

Suppose two Gaussian classes have the same population mean and equal prior probabilities, but different covariance matrices. At the population level, can LDA distinguish the classes? Can QDA? Explain how their covariance assumptions shape the decision boundaries.

This question is adapted from a related question contributed by rruiz35.

---
id: w07-pool-03-qda-feature-screening
title: "How Many Predictors Can QDA Use?"
---

In the Week 7 lecture, ordinary QDA could not use all 256 pixel predictors when each digit class had only 100 training images: its within-class covariance matrices were singular. Homework 7 uses the full training file; selecting 40 pixels allows QDA to fit, while selecting 10 gives higher test error. How should we choose the screening rule and number of pixels so the covariance estimates are stable without losing class information or using test data to make these choices? When must screening and the choice of dimension happen during cross-validation, and why?

This question is adapted from related questions contributed by stewary2 and owenp3.

---
id: w07-pool-04-discriminant-vs-logistic
title: "Discriminant Analysis or Logistic Regression?"
---

For a binary outcome, logistic regression directly models $P(Y=1\mid\mathbf X=\mathbf x)$, while discriminant analysis models the distribution of $\mathbf X$ within each class and then applies Bayes' rule. What are the advantages and disadvantages of each route? When might you expect one to outperform the other?

This question is contributed by nkalele2.

---
id: w07-pool-05-knn-vote-tie-breakers
title: "How Should KNN Break a Tied Vote?"
---

Suppose a binary $k$-nearest-neighbor classifier uses an even $k$, and the selected neighbors split evenly between the two classes. Propose two rules for breaking the vote tie. What are the advantages and disadvantages of each, particularly for reproducibility and whether the rule favors one class?

This question is contributed by the instructor, motivated by a Week 6 question on KNN probability ties from haorans9.

---
id: w07-pool-06-brier-loss-vs-accuracy
title: "Does Lower Brier Loss Guarantee Higher Accuracy?"
---

Two binary classifiers are evaluated on the same observations. Model A has lower average Brier loss than Model B. Must Model A also have higher classification accuracy when both use a cutoff of 0.5? Explain or construct a counterexample.

This question is adapted from a related question contributed by schuang7 in Week 6.

---
id: w07-pool-07-test-error-uncertainty
title: "Is a One-Point Test Error Difference Meaningful?"
---

Two KNN models using different distance metrics have test error rates of 12.5% and 13.5% on the same test set. Is that enough to conclude that the first model will predict better on new observations? If not, how would you assess whether the difference reflects a real performance gap or random variation?

This question is adapted from a related question contributed by panico2 in Week 6.

---
id: w07-pool-08-covariance-blend
title: "Should Classes Share Covariance Information?"
---

For each class $k$, let $\widehat{\boldsymbol\Sigma}_k$ be its sample covariance matrix, and let $\widehat{\boldsymbol\Sigma}$ be the pooled covariance matrix. A Gaussian classifier replaces each class covariance in its discriminant score with

$$
\widehat{\boldsymbol\Sigma}_k(\lambda)
=(1-\lambda)\widehat{\boldsymbol\Sigma}_k
+\lambda\widehat{\boldsymbol\Sigma},
\qquad 0\leq\lambda\leq1.
$$

What classifiers result at the two endpoints? Explain the potential advantages and disadvantages of choosing an intermediate value of $\lambda$. How would your answer depend on the class sample sizes and on whether the true class covariances differ?

This question is contributed by the instructor.

---
id: w07-pool-09-lda-logistic-large-sample
title: "Will LDA and Logistic Regression Eventually Agree?"
---

For binary classification, both LDA and logistic regression produce linear decision boundaries and estimate $P(Y=1\mid\mathbf X=\mathbf x)$. A student argues: "Because they solve the same classification problem, as the training sample grows their decision rules must converge to the same rule." Is this necessarily true when the same probability cutoff is used? Explain why or why not, and state conditions under which you would expect the two rules to agree in the large-sample limit.

This question is contributed by the instructor.
