#!/usr/bin/env python3
"""Seed 10 fake questions into a course database for testing."""
import sys
import os
import sqlite3

if len(sys.argv) < 2:
    print("Usage: python3 seed-questions.py <course_slug>")
    sys.exit(1)

slug = sys.argv[1]

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
db_path = os.path.join(DATA_DIR, slug, 'popping.db')

if not os.path.exists(db_path):
    print(f"Error: database not found at {db_path}")
    print("Run init-db.sh first.")
    sys.exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Get course id
course = conn.execute('SELECT id FROM courses LIMIT 1').fetchone()
if not course:
    print("Error: no course found in database")
    sys.exit(1)
course_id = course['id']

questions = [
    "Explain the key differences between bagging and boosting. How does each method reduce variance and bias?",
    "A random forest reports high feature importance for X1 and X2. Discuss why this does not imply causality.",
    "In a random forest with B trees, as B increases: does bias change? Does variance change? Justify mathematically.",
    "In gradient boosting, what happens when the learning rate is very small (0.001) vs very large (1.0)?",
    "XGBoost adds regularization: Ω(f) = γT + ½λΣwⱼ². How does γ control tree complexity?",
    "In AdaBoost, misclassified examples get higher weights. Why does this focus on 'hard' examples?",
    "Compare stacking and blending: how are base predictions combined? What is the role of the holdout set?",
    "Explain why out-of-bag (OOB) error is an unbiased estimate. What are its limitations vs k-fold CV?",
    "The universal approximation theorem says a single hidden layer can approximate any function. Why prefer deep networks?",
    "A NN has 99% train accuracy but 72% validation. Propose 3 regularization strategies (not dropout) and explain."
]

# Clear existing questions
conn.execute('DELETE FROM questions WHERE course_id = ?', (course_id,))

# Insert new questions
for i, q in enumerate(questions, 1):
    conn.execute(
        'INSERT INTO questions (course_id, question_num, question_text) VALUES (?, ?, ?)',
        (course_id, i, q)
    )

conn.commit()
conn.close()

print(f"Seeded {len(questions)} questions into {slug}")
