"""
Day 14 — AI Evaluation & Benchmarking Pipeline
AICB-P1: AI Practical Competency Program, Phase 1

 concepts from lecture:
    - Evaluation = Scientific Method for AI (Hypothesis → Experiment → Measure → Conclude → Iterate)
    - 4 nhóm metrics: Task Completion, Answer Quality, RAG-Specific, Business
    - RAG pipeline metrics: Context Recall → Context Precision → Faithfulness → Answer Relevancy
    - LLM-as-Judge: rubric scoring 1-5, detect bias (positional, verbosity, self-preference)
    - Golden dataset: stratified sampling (5 Easy + 7 Medium + 5 Hard + 3 Adversarial)
    - Failure taxonomy: hallucination, irrelevant, incomplete, off_topic, refusal
    - 5 Whys method for root cause analysis
    - CI/CD integration: eval as quality gate (score < threshold = block deploy)
    - Continuous Improvement Loop: Evaluate → Analyze → Improve → Augment → Repeat

Instructions:
    1. Fill in every required section marked with TODO.
    2. Do NOT change class/function signatures. The optional ``contexts``
       parameter in ``run_full_eval`` is part of the required interface.
    3. Copy this file to solution/solution.py when done.
    4. Run: pytest tests/ -v

The reranking helper is an optional bonus exercise and may remain unimplemented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Task 1 — Data Models (Golden Dataset + Evaluation Results)
# ---------------------------------------------------------------------------

@dataclass
class QAPair:
    """
    A question-answer pair for evaluation (part of the Golden Dataset).

    From lecture: Golden dataset cần có:
        - question: câu hỏi user
        - ground_truth (expected_answer): expert-written expected answer
        - context: source documents cần retrieve
        - metadata: difficulty (easy/medium/hard), category, source_docs

    Fields:
        question:        The question to answer.
        expected_answer: The reference/ground-truth answer (expert-written).
        context:         Source context (may be empty string if not applicable).
        metadata:        Optional metadata dict (difficulty, category, etc.).
        retrieved_contexts: List of retrieved chunks (ORDER = retriever rank).
                            Used by the retrieval-side metrics (Task 2b).
    """
    question: str
    expected_answer: str
    context: str = ""
    metadata: dict = field(default_factory=dict)
    retrieved_contexts: list = field(default_factory=list)

@dataclass
class EvalResult:
    """
    Evaluation result for a single Q&A pair.
    """

    qa_pair: QAPair | None
    actual_answer: str
    faithfulness: float
    relevance: float
    completeness: float
    passed: bool
    failure_type: str | None = None
    context_precision: float | None = None
    context_recall: float | None = None

    def overall_score(self) -> float:
        return (
            self.faithfulness
            + self.relevance
            + self.completeness
        ) / 3.0



# ---------------------------------------------------------------------------
# Task 2 — RAGAS Evaluator (Simplified word-overlap heuristic)
# ---------------------------------------------------------------------------
# In production, replace with actual RAGAS framework:
#   from ragas import evaluate
#   from ragas.metrics import Faithfulness, AnswerRelevancy, ContextRecall, ContextPrecision
#
# Or DeepEval:
#   from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric
#   assert_test(test_case, [faithfulness, hallucination])
#
# Or TruLens:
#   from trulens.core import Feedback
#   f_groundedness = Feedback(provider.groundedness_measure_with_cot_reasons)
# ---------------------------------------------------------------------------

# Common English stopwords are ignored so overlap reflects *content* words,
# not filler (otherwise "is"/"a"/"the" inflate every score).
STOPWORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "as", "by", "and", "or",
    "it", "its", "this", "that", "these", "those", "from", "into", "than",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokenization, ignoring punctuation and stopwords."""
    if not text:
        return set()
    tokens = re.findall(r"\b\w+\b", text.lower())
    return {t for t in tokens if t not in STOPWORDS}


class RAGASEvaluator:
    """
    Evaluates RAG pipeline outputs using RAGAS-inspired heuristics.

    All metrics use word overlap rather than LLM calls for simplicity.
    Replace with actual LLM-based evaluation in production.
    """

    def evaluate_faithfulness(self, answer: str, context: str) -> float:
        """
        Measure how grounded the answer is in the context.

        Heuristic:
            answer_tokens = _tokenize(answer)
            context_tokens = _tokenize(context)
            faithfulness = |answer_tokens ∩ context_tokens| / |answer_tokens|
            Clamp to [0.0, 1.0]. Return 1.0 if answer is empty.

        Returns:
            float in [0.0, 1.0] — 1.0 = fully grounded in context.
        """
        answer_tokens = _tokenize(answer)
        context_tokens = _tokenize(context)

        if not answer_tokens:
            return 1.0

        score = len(answer_tokens & context_tokens) / len(answer_tokens)
        return max(0.0, min(1.0, score))

    def evaluate_relevance(self, answer: str, question: str) -> float:
        """
        Measure how relevant the answer is to the question.

        Heuristic:
            relevance = |answer_tokens ∩ question_tokens| / |question_tokens|
            Clamp to [0.0, 1.0]. Return 1.0 if question is empty.

        Returns:
            float in [0.0, 1.0]
        """
        answer_tokens = _tokenize(answer)
        question_tokens = _tokenize(question)

        if not question_tokens:
            return 1.0

        score = len(answer_tokens & question_tokens) / len(question_tokens)
        return max(0.0, min(1.0, score))

    def evaluate_completeness(self, answer: str, expected: str) -> float:
        """
        Measure how well the answer covers the expected answer.

        Heuristic:
            completeness = |answer_tokens ∩ expected_tokens| / |expected_tokens|
            Clamp to [0.0, 1.0]. Return 1.0 if expected is empty.

        Returns:
            float in [0.0, 1.0]
        """
        answer_tokens = _tokenize(answer)
        expected_tokens = _tokenize(expected)

        if not expected_tokens:
            return 1.0

        score = len(answer_tokens & expected_tokens) / len(expected_tokens)
        return max(0.0, min(1.0, score))

    # -----------------------------------------------------------------------
    # Task 2b — Retrieval-side metrics (evaluate the GET-CONTEXT step)
    # -----------------------------------------------------------------------
    # From lecture (RAG pipeline): Context Recall → Context Precision →
    #   Faithfulness → Answer Relevancy. The two below score the RETRIEVER,
    #   operating on a LIST of chunks (order = retriever rank).
    # -----------------------------------------------------------------------

    def evaluate_context_recall(self, contexts: list[str], expected: str) -> float:
        """Context Recall — how much of the expected answer is covered by the
        UNION of retrieved chunks.

        Heuristic:
            union_tokens = ⋃ _tokenize(chunk) for chunk in contexts
            recall = |expected_tokens ∩ union_tokens| / |expected_tokens|
            Clamp to [0.0, 1.0]. Return 1.0 if expected is empty.

        Low recall => retriever missed evidence the answer needs.
        """
        expected_tokens = _tokenize(expected)

        if not expected_tokens:
            return 1.0

        union_tokens: set[str] = set()
        for chunk in contexts:
            union_tokens.update(_tokenize(chunk))

        score = len(expected_tokens & union_tokens) / len(expected_tokens)
        return max(0.0, min(1.0, score))

    def evaluate_context_precision(
        self,
        contexts: list[str],
        expected: str,
        relevance_threshold: float = 0.1,
    ) -> float:
        """Context Precision — RANK-AWARE Average Precision (AP@K), like RAGAS.
        Rewards retrievers that place RELEVANT chunks BEFORE noise.

        Steps:
            1. A chunk is "relevant" if it covers >= relevance_threshold of the
               expected tokens:  |chunk ∩ expected| / |expected| >= threshold
            2. Precision@k = (#relevant in top-k) / k
            3. AP@K = (1 / #relevant) * Σ_k [ Precision@k · relevant_k ]

        Return 1.0 if expected empty; 0.0 if no chunks or none relevant.
        Reordering relevant chunks earlier (reranking) raises this score.
        """
        expected_tokens = _tokenize(expected)

        if not expected_tokens:
            return 1.0

        if not contexts:
            return 0.0

        relevant_flags: list[bool] = []

        for chunk in contexts:
            chunk_tokens = _tokenize(chunk)
            chunk_recall = (
                len(chunk_tokens & expected_tokens) / len(expected_tokens)
            )
            relevant_flags.append(chunk_recall >= relevance_threshold)

        num_relevant = sum(relevant_flags)

        if num_relevant == 0:
            return 0.0

        relevant_so_far = 0
        precision_sum = 0.0

        for k, is_relevant in enumerate(relevant_flags, start=1):
            if is_relevant:
                relevant_so_far += 1
                precision_at_k = relevant_so_far / k
                precision_sum += precision_at_k

        score = precision_sum / num_relevant
        return max(0.0, min(1.0, score))

    def run_full_eval(
        self,
        answer: str,
        question: str,
        context: str,
        expected: str,
        contexts: list[str] | None = None,
    ) -> EvalResult:
        """
        Run the three answer-side evaluations and, when ``contexts`` is
        supplied, both retrieval-side evaluations.

        passed = True if all three scores >= 0.5.

        failure_type determination (first match wins):
            faithfulness < 0.3  → "hallucination"
            relevance < 0.3     → "irrelevant"
            completeness < 0.3  → "incomplete"
            otherwise if failed → "off_topic"

        Retrieval wiring:
            contexts is None → context_recall and context_precision stay None
            contexts provided → evaluate and store both retrieval metrics

        The two retrieval metrics diagnose the retriever and do not change the
        three-metric ``passed`` rule or ``overall_score()``.

        Returns:
            EvalResult with all fields populated.
        """
        faithfulness = self.evaluate_faithfulness(answer, context)
        relevance = self.evaluate_relevance(answer, question)
        completeness = self.evaluate_completeness(answer, expected)

        passed = (
            faithfulness >= 0.5
            and relevance >= 0.5
            and completeness >= 0.5
        )

        if faithfulness < 0.3:
            failure_type = "hallucination"
        elif relevance < 0.3:
            failure_type = "irrelevant"
        elif completeness < 0.3:
            failure_type = "incomplete"
        elif not passed:
            failure_type = "off_topic"
        else:
            failure_type = None

        context_recall = None
        context_precision = None

        if contexts is not None:
            context_recall = self.evaluate_context_recall(
                contexts, expected
            )
            context_precision = self.evaluate_context_precision(
                contexts, expected
            )

        return EvalResult(
            qa_pair=None,
            actual_answer=answer,
            faithfulness=faithfulness,
            relevance=relevance,
            completeness=completeness,
            passed=passed,
            failure_type=failure_type,
            context_precision=context_precision,
            context_recall=context_recall,
        )




# ---------------------------------------------------------------------------
# Reranking helper (used by Exercise 3.5 — boosting Context Precision)
# ---------------------------------------------------------------------------

def rerank_by_overlap(contexts: list[str], query: str) -> list[str]:
    """A minimal lexical reranker: sort chunks by word overlap with the query,
    most-overlapping first. Stand-in for a real cross-encoder reranker.

    Reordering relevant chunks toward the top increases the rank-aware
    Context Precision WITHOUT changing the retrieved set.
    """
    query_tokens = _tokenize(query)

    return sorted(
        contexts,
        key=lambda c: len(_tokenize(c) & query_tokens),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Task 3 — LLM Judge
# ---------------------------------------------------------------------------
# From lecture:
#   - Judge LLM nhận: question + agent answer + reference answer + rubric
#   - Judge trả về: Score 1-5 + Rationale
#   - Best practices: multiple judges, randomize order, calibrate against human
#   - Biases: positional, verbosity, self-preference
#   - Rubric template:
#       5 = Correct, complete, well-cited
#       4 = Mostly correct, minor gaps
#       3 = Partially correct, some errors
#       2 = Significant errors or missing info
#       1 = Wrong or irrelevant
# ---------------------------------------------------------------------------

class LLMJudge:
    """
    Uses an LLM to score AI responses according to a rubric.
    """

    def __init__(self, judge_llm_fn: Callable[[str], str]) -> None:
        self.judge_llm_fn = judge_llm_fn

    def score_response(
        self,
        question: str,
        answer: str,
        rubric: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Score an AI response using the judge LLM.
        """
        rubric_text = "\n".join(
            f"- {criterion}: {description}"
            for criterion, description in rubric.items()
        )

        prompt = f"""
            You are an objective evaluator of an AI response.

            Question:
            {question}

            AI Answer:
            {answer}

            Evaluation Rubric:
            {rubric_text}

            Score each criterion from 1 to 5:
            5 = Correct, complete, well-cited
            4 = Mostly correct, minor gaps
            3 = Partially correct, some errors
            2 = Significant errors or missing information
            1 = Wrong or irrelevant

            Return ONLY valid JSON in this format:
            {{
            "scores": {{
                "criterion_name": 1,
                "another_criterion": 4
            }},
            "reasoning": "Brief explanation of the scores."
            }}
        """.strip()

        raw_response = self.judge_llm_fn(prompt)

        default_scores = {
            criterion: 0.5
            for criterion in rubric
        }

        try:
            # Handle occasional markdown code fences around JSON.
            json_text = raw_response.strip()

            if json_text.startswith("```"):
                json_text = re.sub(
                    r"^```(?:json)?\s*|\s*```$",
                    "",
                    json_text,
                    flags=re.IGNORECASE,
                ).strip()

            # Extract the JSON object if the LLM included extra text.
            match = re.search(r"\{.*\}", json_text, re.DOTALL)
            if match:
                json_text = match.group(0)

            import json

            parsed = json.loads(json_text)
            raw_scores = parsed.get("scores", {})

            scores: dict[str, float] = {}

            for criterion in rubric:
                value = raw_scores.get(criterion)

                if value is None:
                    scores[criterion] = 0.5
                    continue

                value = float(value)

                # Rubric scores are 1-5; normalize to 0-1.
                if value > 1.0:
                    value = (value - 1.0) / 4.0

                scores[criterion] = max(0.0, min(1.0, value))

            reasoning = parsed.get("reasoning", raw_response)

            return {
                "scores": scores,
                "reasoning": str(reasoning),
            }

        except (ValueError, TypeError, Error, AttributeError):
            return {
                "scores": default_scores,
                "reasoning": raw_response,
            }

    def detect_bias(self, scores_batch: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Detect potential bias patterns in a batch of judge scores.
        """
        result = {
            "positional_bias": False,
            "leniency_bias": False,
            "severity_bias": False,
        }

        if not scores_batch:
            return result

        # Collect all normalized criterion scores.
        all_scores: list[float] = []

        for item in scores_batch:
            scores = item.get("scores", {})
            if not isinstance(scores, dict):
                continue

            for value in scores.values():
                try:
                    score = float(value)

                    # Accept either normalized 0-1 or raw 1-5 scores.
                    if score > 1.0:
                        score = (score - 1.0) / 4.0

                    all_scores.append(max(0.0, min(1.0, score)))
                except (TypeError, ValueError):
                    continue

        if all_scores:
            average = sum(all_scores) / len(all_scores)
            result["leniency_bias"] = average > 0.8
            result["severity_bias"] = average < 0.3

        # Positional bias can only be detected when the batch contains
        # explicit first/second position scores. Support common representations.
        first_scores: list[float] = []
        second_scores: list[float] = []

        for item in scores_batch:
            first = item.get("first_score")
            second = item.get("second_score")

            if first is None or second is None:
                continue

            try:
                first = float(first)
                second = float(second)

                if first > 1.0:
                    first = (first - 1.0) / 4.0
                if second > 1.0:
                    second = (second - 1.0) / 4.0

                first_scores.append(first)
                second_scores.append(second)
            except (TypeError, ValueError):
                continue

        if first_scores and second_scores:
            first_avg = sum(first_scores) / len(first_scores)
            second_avg = sum(second_scores) / len(second_scores)

            # "Consistently scores higher" means average first-position
            # score is higher than second-position score.
            result["positional_bias"] = first_avg > second_avg

        return result

# ---------------------------------------------------------------------------
# Task 4 — Benchmark Runner
# ---------------------------------------------------------------------------
# From lecture:
#   - CI/CD integration: Framework + CI/CD = quality gate tự động
#   - Agent với faithfulness < 0.7 → không được deploy
#   - Regression = metric drop > 0.05 vs baseline
#   - Triggers: mỗi code release, mỗi prompt change, trước demo/launch
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    """
    Runs a full evaluation benchmark.
    """

    def run(
        self,
        qa_pairs: list[QAPair],
        agent_fn: Callable[[str], str],
        evaluator: RAGASEvaluator,
    ) -> list[EvalResult]:
        results: list[EvalResult] = []

        for pair in qa_pairs:
            answer = agent_fn(pair.question)

            result = evaluator.run_full_eval(
                answer=answer,
                question=pair.question,
                context=pair.context,
                expected=pair.expected_answer,
                contexts=pair.retrieved_contexts,
            )

            result.qa_pair = pair
            results.append(result)

        return results

    def generate_report(self, results: list[EvalResult]) -> dict[str, Any]:
        """
        Generate an aggregate report from evaluation results.
        """
        total = len(results)
        passed = sum(1 for result in results if result.passed)

        def average(attribute: str) -> float:
            if not results:
                return 0.0

            values = [
                getattr(result, attribute)
                for result in results
                if getattr(result, attribute, None) is not None
            ]

            return sum(values) / len(values) if values else 0.0

        retrieval_average = lambda attribute: (
            sum(
                value
                for value in (
                    getattr(result, attribute, None)
                    for result in results
                )
                if value is not None
            )
            / sum(
                1
                for result in results
                if getattr(result, attribute, None) is not None
            )
            if any(
                getattr(result, attribute, None) is not None
                for result in results
            )
            else None
        )

        failure_types: dict[str, int] = {}

        for result in results:
            failure_type = getattr(result, "failure_type", None)

            if failure_type:
                failure_types[failure_type] = (
                    failure_types.get(failure_type, 0) + 1
                )

        return {
            "total": total,
            "passed": passed,
            "pass_rate": passed / total if total else 0.0,
            "avg_faithfulness": average("faithfulness"),
            "avg_relevance": average("relevance"),
            "avg_completeness": average("completeness"),
            "avg_context_recall": retrieval_average("context_recall"),
            "avg_context_precision": retrieval_average("context_precision"),
            "failure_types": failure_types,
        }

    def run_regression(
        self,
        new_results: list,
        baseline_results: list,
    ) -> dict:
        """Compare new evaluation results against a baseline."""
        metrics = (
            "faithfulness",
            "relevance",
            "completeness",
        )

        def avg(results: list, metric: str) -> float:
            if not results:
                return 0.0

            values = [
                getattr(result, metric)
                for result in results
                if getattr(result, metric, None) is not None
            ]

            return sum(values) / len(values) if values else 0.0

        new_avgs = {
            metric: avg(new_results, metric)
            for metric in metrics
        }

        baseline_avgs = {
            metric: avg(baseline_results, metric)
            for metric in metrics
        }

        regressions = [
            metric
            for metric in metrics
            if baseline_avgs[metric] - new_avgs[metric] > 0.05
        ]

        return {
            "new_avg_faithfulness": new_avgs["faithfulness"],
            "new_avg_relevance": new_avgs["relevance"],
            "new_avg_completeness": new_avgs["completeness"],
            "baseline_avg_faithfulness": baseline_avgs["faithfulness"],
            "baseline_avg_relevance": baseline_avgs["relevance"],
            "baseline_avg_completeness": baseline_avgs["completeness"],
            "regressions": regressions,
            "passed": len(regressions) == 0,
        }

    def identify_failures(
        self,
        results: list[EvalResult],
        threshold: float = 0.5,
    ) -> list[EvalResult]:
        """
        Return EvalResults where any score is below threshold.
        """
        return [
            result
            for result in results
            if (
                result.faithfulness < threshold
                or result.relevance < threshold
                or result.completeness < threshold
            )
        ]

# ---------------------------------------------------------------------------
# Task 5 — Failure Analyzer
# ---------------------------------------------------------------------------
# From lecture:
#   Failure Taxonomy:
#     - hallucination: bịa thông tin → faithfulness guardrail yếu
#     - irrelevant: không giải quyết câu hỏi → prompt ambiguous
#     - incomplete: bỏ sót thông tin → context window nhỏ, retrieval thiếu
#     - off_topic: trả lời chủ đề khác → intent detection sai
#     - refusal: từ chối khi nên trả lời → guardrails quá chặt
#
#   5 Whys Method: hỏi "Tại sao?" liên tục cho đến root cause
#   Failure Clustering: fix 1 root cause giải quyết nhiều failures cùng lúc
#   Continuous Improvement: Evaluate → Analyze → Improve → Augment → Repeat
# ---------------------------------------------------------------------------

class FailureAnalyzer:
    """
    Analyzes failed evaluation results to identify patterns and suggest fixes.
    """

    def categorize_failures(
        self, failures: list[EvalResult]
    ) -> dict[str, int]:
        """
        Count failures by failure_type.
        """
        categories: dict[str, int] = {}

        for failure in failures:
            failure_type = getattr(failure, "failure_type", None)

            if failure_type:
                categories[failure_type] = (
                    categories.get(failure_type, 0) + 1
                )

        return categories

    def find_root_cause(self, failure: EvalResult) -> str:
        """
        Suggest a root cause for a single failure based on its scores.
        """
        scores = {
            "faithfulness": failure.faithfulness,
            "relevance": failure.relevance,
            "completeness": failure.completeness,
        }

        lowest_metric = min(scores, key=scores.get)
        lowest_score = scores[lowest_metric]

        # Only treat a metric as the root cause when it is actually
        # below the evaluation threshold.
        failing_metrics = [
            metric
            for metric, score in scores.items()
            if score < 0.5
        ]

        if len(failing_metrics) > 1:
            return (
                "Multiple issues detected — review full pipeline"
            )

        if lowest_metric == "faithfulness" and lowest_score < 0.5:
            return (
                "Context is missing or irrelevant — improve retrieval"
            )

        if lowest_metric == "relevance" and lowest_score < 0.5:
            return (
                "Answer does not address the question — improve prompt clarity"
            )

        if lowest_metric == "completeness" and lowest_score < 0.5:
            return (
                "Answer is missing  information — increase context window "
                "or improve generation"
            )

        return "Multiple issues detected — review full pipeline"

    def generate_improvement_log(
        self,
        failures: list,
        suggestions: list[str],
    ) -> str:
        """Generate a Markdown table logging failures and improvement actions.
        """
        lines = [
            "| Failure ID | Type | Root Cause | Suggested Fix | Status |",
            "|------------|------|------------|---------------|--------|",
        ]

        for index, failure in enumerate(failures, start=1):
            failure_id = f"F{index:03d}"
            failure_type = getattr(failure, "failure_type", None) or "unknown"
            root_cause = self.find_root_cause(failure)

            if index <= len(suggestions):
                suggestion = suggestions[index - 1]
            else:
                suggestion = "Review failure and improve the relevant pipeline stage"

            # Keep table formatting intact if generated text contains pipes.
            failure_type = str(failure_type).replace("|", "\\|")
            root_cause = str(root_cause).replace("|", "\\|")
            suggestion = str(suggestion).replace("|", "\\|")

            lines.append(
                f"| {failure_id} | {failure_type} | {root_cause} | "
                f"{suggestion} | Open |"
            )

        return "\n".join(lines)

    def generate_improvement_suggestions(
        self, failures: list[EvalResult]
    ) -> list[str]:
        """
        Generate a prioritized list of improvement suggestions based on
        failure patterns.
        """
        if not failures:
            return []

        categories = self.categorize_failures(failures)
        suggestions: list[str] = []

        if categories.get("hallucination", 0) > 0:
            suggestions.append(
                "Implement a hallucination checker to filter unsupported claims"
            )

        if categories.get("irrelevant", 0) > 0:
            suggestions.append(
                "Improve prompt instructions and intent detection to keep "
                "answers focused on the user's question"
            )

        if categories.get("incomplete", 0) > 0:
            suggestions.append(
                "Increase chunk size or context window and add examples of "
                "complete answers"
            )

        if categories.get("off_topic", 0) > 0:
            suggestions.append(
                "Improve intent classification and add topic-boundary "
                "instructions to the generation prompt"
            )

        if categories.get("refusal", 0) > 0:
            suggestions.append(
                "Review guardrails and refusal rules to avoid unnecessary "
                "refusals for in-scope questions"
            )

        # If there are failures but none has a recognized category,
        # provide general pipeline actions.
        if not suggestions:
            suggestions.extend([
                "Review failed examples and strengthen the evaluation "
                "dataset with representative edge cases",
                "Analyze retrieval quality and improve chunking or reranking",
                "Add regression tests for recurring failure patterns",
            ])

        # The task requests at least 3 suggestions when failures exist.
        defaults = [
            "Add representative failure cases to the golden dataset "
            "for regression testing",
            "Run retrieval and answer metrics separately to isolate "
            "the failing pipeline stage",
            "Add automated evaluation as a CI/CD quality gate before deployment",
        ]

        for suggestion in defaults:
            if len(suggestions) >= 3:
                break
            if suggestion not in suggestions:
                suggestions.append(suggestion)

        return suggestions

# ---------------------------------------------------------------------------
# Entry point for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Sample golden dataset (mini version — use 20 pairs in actual lab)
    # From lecture: stratified sampling = 5 Easy + 7 Medium + 5 Hard + 3 Adversarial
    qa_pairs = [
        # Easy — factual lookup
        QAPair(
            question="What is RAG?",
            expected_answer="RAG stands for Retrieval-Augmented Generation, which combines retrieval with text generation.",
            context="RAG is a technique that retrieves relevant documents and uses them to ground LLM generation.",
            metadata={"difficulty": "easy", "category": "definition"},
        ),
        QAPair(
            question="What is the capital of France?",
            expected_answer="Paris is the capital of France.",
            context="France is a country in Western Europe. Its capital city is Paris.",
            metadata={"difficulty": "easy", "category": "factual"},
        ),
        # Medium — multi-step reasoning
        QAPair(
            question="Explain backpropagation and why it matters for training",
            expected_answer="Backpropagation is an algorithm for training neural networks by computing gradients efficiently, enabling deep learning models to learn from errors.",
            context="Neural networks learn through gradient descent. Backpropagation efficiently computes these gradients layer by layer.",
            metadata={"difficulty": "medium", "category": "explanation"},
        ),
        # Hard — ambiguous
        QAPair(
            question="Should I use RAG or fine-tuning for my chatbot?",
            expected_answer="It depends on the use case: RAG is better for frequently updated knowledge, fine-tuning for consistent style/behavior. Consider cost, latency, and data freshness.",
            context="RAG retrieves external documents at inference time. Fine-tuning modifies model weights during training.",
            metadata={"difficulty": "hard", "category": "comparison"},
        ),
        # Adversarial — out-of-scope
        QAPair(
            question="What is the meaning of life?",
            expected_answer="This question is outside the scope of this system. I can help with AI and technology questions.",
            context="This is an AI assistant specialized in technology topics.",
            metadata={"difficulty": "adversarial", "category": "out_of_scope"},
        ),
    ]

    evaluator = RAGASEvaluator()
    runner = BenchmarkRunner()

    def mock_agent(question: str) -> str:
        """Simple mock agent for testing. Replace with your actual agent."""
        return f"Based on my knowledge: {question[:30]}... The answer involves  concepts."

    # Run benchmark
    results = runner.run(qa_pairs, mock_agent, evaluator)
    report = runner.generate_report(results)
    print("=== Benchmark Report ===")
    for k, v in report.items():
        print(f"  {k}: {v}")

    # Identify and analyze failures
    failures = runner.identify_failures(results, threshold=0.5)
    print(f"\n=== Failures ({len(failures)}) ===")
    analyzer = FailureAnalyzer()

    # Categorize (from lecture: cluster before fix)
    categories = analyzer.categorize_failures(failures)
    print("Failure Categories:", categories)

    # Root cause for each failure (from lecture: 5 Whys)
    for f in failures:
        cause = analyzer.find_root_cause(f)
        print(f"  Root cause: {cause}")

    # Improvement suggestions (from lecture: continuous improvement loop)
    suggestions = analyzer.generate_improvement_suggestions(failures)
    print("\nImprovement Suggestions:")
    for s in suggestions:
        print(f"  - {s}")

    # Generate improvement log (Markdown table)
    log = analyzer.generate_improvement_log(failures, suggestions)
    print("\n=== Improvement Log ===")
    print(log)
