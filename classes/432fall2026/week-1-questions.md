---
id: w01-chahak2-error-distributions-loss-functions
title: "From Error Distributions to Loss Functions"
---

In the linear model $y_i = \mathbf{x}_i^{\mathsf T}\boldsymbol\beta + \varepsilon_i$, suppose the errors are independent with a common fixed scale. Why does a Gaussian error model make maximum-likelihood estimation of $\boldsymbol\beta$ equivalent to minimizing squared error, while a Laplace error model leads to minimizing absolute error? Compare how the two estimators respond to a single large outlier.

This question is combined from: chahak2.

---
id: w01-tingyun3-stable-predictions-unstable-coefficients
title: "Stable Predictions, Unstable Coefficients"
---

Suppose two columns of $\mathbf{X}$ are nearly collinear. Why can small changes in the data produce large changes in the individual least-squares coefficient estimates while the fitted values $\mathbf{X}\widehat{\boldsymbol\beta}$ remain nearly unchanged? Use rank, geometry, or the normal equations in your explanation, and discuss what this means for interpreting individual coefficients versus predicting within the observed data region.

This question is combined from: tingyun3, gjia3, slxia2.

---
id: w01-stewary2-r-squared-extra-predictors
title: "Why Training R-Squared Rewards Extra Predictors"
---

Two nested ordinary least-squares models are fit to the same response on the same observations, and both include an intercept. Why can adding predictors never increase the training residual sum of squares and therefore never decrease $R^2$? Why can this property make training $R^2$ misleading for comparing models of different sizes, and what alternative check would better assess whether the added predictors improve prediction?

This question is combined from: stewary2.

---
id: w01-shushim2-working-code-wrong-answer
title: "When Working Code Gives the Wrong Answer"
---

An AI provides a confident statistical explanation and code that runs and produces plausible output. What kinds of errors could still remain? Design a short verification workflow that combines at least two independent checks, such as mathematical reasoning, documentation, a fixed-seed simulation, or a deliberately chosen counterexample, and explain what each check can reveal that simply rerunning the code cannot.

This question is combined from: shushim2, cudzich3, calebsg3, heta2.

---
id: w01-wenhao7-llm-agreement-verification
title: "When LLM Agreement Is Not Independent Verification"
---

Suppose several large language models give the same answer to a statistical question. Under what conditions is that agreement weak evidence because the models may have correlated errors? Distinguish agreement among models from verification using independent evidence, and propose one external check that could overturn an incorrect consensus.

This question is combined from: wenhao7.

---
id: w01-ericc13-misleading-validation
title: "When Good Validation Performance Is Misleading"
---

An AI agent recommends a model because it performs well under a chosen validation scheme. How could data leakage, temporal dependence, nonrepresentative data, distribution shift, or a mismatch between the metric and the application make that performance misleading? Choose one application and propose a validation or stress-testing design that better matches its intended use.

This question is combined from: ericc13, uvashi2.

---
id: w01-panico2-random-seed-control
title: "What Does a Random Seed Actually Control?"
---

Homework 01 asks everyone to set a random seed before a simulation. What does setting the seed control? Compare what should happen when the same code is rerun in the same software environment with (i) the same seed and (ii) no fixed seed. Why does this matter when another person tries to reproduce the result?

This question is combined from: panico2, bpham9, kyang53, melikah2.

---
id: w01-connerj2-same-seed-different-results
title: "Same Seed, Different Results"
---

Two researchers both use seed 43201 but obtain different estimates, perhaps because one uses R and the other Python. Alternatively, the same R analysis produces a different result several months later. Why is matching the seed not sufficient? Develop an ordered diagnostic checklist that considers the random-number generator, software and package versions, data and preprocessing, and numerical methods. What should be saved or reported so another person can reproduce the analysis?

This question is combined from: connerj2, allyw2, ousher2.

---
id: w01-zexuanj2-agent-versus-chatbot
title: "AI Agent or Chatbot?"
---

Imagine the same language model in two settings: one only replies to a user's messages, while the other can use tools or packaged skills, keep track of a multi-step task, and take actions. What criteria would you use to call the second system an AI agent rather than a chatbot? Give one task for which a chatbot is preferable and one for which an agent is preferable, and explain why.

This question is combined from: zexuanj2, cs148.

---
id: w01-prerith2-agent-harness-outcomes
title: "How Does an Agent Harness Change the Outcome?"
---

Suppose the same model receives the same prompt and skill but runs in two different agent harnesses. One harness offers many tools, repeated retries, and a large reasoning budget; the other exposes only the needed tools and uses a clear stopping rule. How can these choices change completeness, cost, and failure modes? For one simple task and one complex task, choose a harness configuration and justify your design.

This question is combined from: prerith2, amoghu2, lugong2.

---
id: w01-ziqiz13-local-commit-shared-history
title: "From Local Commit to Shared GitHub History"
---

Suppose you edit a project locally. What information is stored when you run `git commit`, and what changes only after `git push`? How does Git know which GitHub repository should receive the push, and why is this local commit and remote push workflow more useful for collaboration and reproducibility than uploading files through a browser? How can multiple contributors work without simply overwriting one another?

This question is combined from: ziqiz13, st58, mp79, ryank11, lb16.

---
id: w01-ruitong7-agent-sensitive-data-boundaries
title: "Safe Boundaries for Agents and Sensitive Data"
---

Suppose a local AI agent is asked to clean and model confidential human-subjects data while also having access to files and development tools. What is the minimum data and tool access it should receive? Design safeguards involving raw versus deidentified data, file and network permissions, human approval, logging, and containment. What important risk would remain even after these safeguards are applied?

This question is combined from: ruitong7, rruiz35, selsa3.

---
id: w01-jingyi64-human-skills-ai
title: "Human Skills That Matter More With AI"
---

As AI handles more coding, routine analysis, and written explanation, which human skills become more, rather than less, important in statistical work? Choose two or three skills, such as problem formulation, domain judgment, communication of uncertainty, or responsibility for consequences, and explain why they are difficult to delegate. How should this change what students practice?

This question is combined from: jingyi64.

---
id: w01-runting5-learning-with-ai
title: "Learning With AI Without Dependence"
---

How can a student use AI to rebuild rusty prerequisites and coding skills while preserving an integrated understanding and independent problem-solving ability? Propose a repeatable learning cycle that specifies what the student should attempt independently, when AI should provide explanation or feedback, and how the student should test the resulting understanding on a new problem. What warning signs would indicate that AI is producing fragmented knowledge or dependence rather than genuine learning?

This question is combined from: runting5, rainas3, maeved2.
