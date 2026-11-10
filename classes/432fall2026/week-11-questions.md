---
id: w11-pool-01-explained-variance-and-group-separation
title: "Can the Leading Components Miss the Separation Between Two Groups?"
---

Suppose a dataset contains two distinct groups. Under what conditions could the first several principal components explain most of the total variance, yet the two groups overlap when projected onto those components? Give an example.

This question is adapted from related questions contributed by brianc19, kum2, and amoghu2.

---
id: w11-pool-02-rescaling-one-variable
title: "Can Rescaling One Variable Change the First Principal Component?"
---

Suppose we fit PCA to several centered variables in their original units. We then change one variable from meters to centimeters and fit PCA again. Can this change the direction of the first principal component? Would your answer change if we standardized every variable to unit variance before each fit?

This question is adapted from related questions contributed by cgao19 and schuang7.

---
id: w11-pool-03-comparing-clustering-error
title: "Does a Lower Clustering Error Mean PCA Helped?"
---

Two students use K-means with the same $K$ on the same standardized data. One uses all principal components and the other keeps only the first two. The second student reports a smaller within-cluster sum of squares. Could that happen even if both students assigned every observation to exactly the same cluster? How could they compare their partitions on a common scale?

This question is adapted from related questions contributed by shushim2 and selsa3.

---
id: w11-pool-04-components-for-regression
title: "How Many PCs Should a Regression Keep?"
---

Five PCs explain 95% of the predictor variance. Adding a sixth PC barely changes that percentage, yet it lowers cross-validated prediction error. How could PC6 improve prediction? If adding still more PCs keeps lowering training error, what would make you stop?

This question is adapted from related questions contributed by melikah2, kyang53, and tingyun3.

---
id: w11-pool-05-duplicating-a-variable
title: "Can Copying a Variable Change the Clusters?"
---

We standardize several car measurements and use Euclidean distance for hierarchical clustering. Someone accidentally includes the horsepower column twice. How does this change the squared distance between two cars? Could it change which cars merge first, even though the copy contains no new information?

This question is adapted from a related question contributed by zexuanj2.

---
id: w11-pool-06-moving-one-point
title: "Can Moving One Point Improve a Converged K-means Fit?"
---

A two-cluster K-means fit has converged, with each point assigned to its nearest center. A student moves one point to the other cluster, recomputes both centers, and obtains a smaller within-cluster sum of squares. Is that possible? Explain how the student's move differs from an ordinary K-means assignment step.

This question is adapted from a related question contributed by dehuili2.

---
id: w11-pool-07-choosing-the-number-of-clusters
title: "How Should We Choose the Number of Clusters?"
---

Suppose we fit K-means to the same data for increasing values of $K$. As $K$ increases, can the smallest possible within-cluster sum of squares ever increase? What happens when each observation has its own cluster? Given this behavior, how would you choose $K$ in practice?

This question is adapted from a related question contributed by zm25.

---
id: w11-pool-08-continuous-and-categorical-variables
title: "How Should We Balance Horsepower and Transmission in a Clustering Distance?"
---

Suppose we cluster cars using horsepower, a continuous variable, and transmission type, a categorical variable (manual or automatic). How would you define a distance that uses both? In your distance, what difference in horsepower would count as much as a difference in transmission type, and how would you choose that trade-off for the purpose of the analysis?

This question is adapted from a related question contributed by hx27.

---
id: w11-pool-09-testing-a-k-means-function
title: "How Would You Test an AI-Written K-means Function?"
---

An AI-written K-means function runs without errors, but two runs return different groups. Choose a small dataset and fix the starting centers. What would you calculate by hand after one iteration to distinguish a coding error from ordinary sensitivity to initialization?

This question is adapted from related questions contributed by jyan36 and bugatha2.

---
id: w11-pool-10-equally-optimal-partitions
title: "Can Two Different K-means Answers Both Be Optimal?"
---

For the same data and the same $K$, can two different groupings both minimize the within-cluster sum of squares? If so, how would you choose between them?

This question is adapted from related questions contributed by st58 and bpham9.

---
id: w11-pool-11-outlier-and-cluster-centers
title: "Can One Outlier Make K-means Merge Two Clear Groups?"
---

On a number line, ten observations sit near 0 and ten sit near 5. We fit K-means with $K=2$, then add one observation at 100 and refit. Could the new point get a center all to itself while the two original groups share the other center?

This question is adapted from a related question contributed by mannat2.

---
id: w11-pool-12-cross-validation-for-clustering
title: "What Would Cross-Validation Actually Validate in Clustering?"
---

For each candidate $K$, a student fits K-means on the training data in each cross-validation fold. The student then averages the squared distances of all held-out points to their nearest centers from the corresponding training fits. Suppose $K=10$ beats $K=3$. What has the ten-center fit done better? Is that evidence that the population contains ten groups?

This question is adapted from a related question contributed by yanxif2.

---
id: w11-pool-13-labels-and-feature-selection
title: "Can an Analyst Accidentally Build the Answer Into an Unsupervised Analysis?"
---

A researcher tries several sets of genes, keeps the set whose clusters agree best with known tissue labels, and reports that clustering independently recovered the tissue types. Where did the labels enter the analysis? How could the researcher test the claim on new samples?

This question is adapted from related questions contributed by mp79 and calebsg3.
