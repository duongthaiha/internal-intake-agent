import unittest

from scripts.evaluate_local import (
    DEFAULT_CASES_PATH,
    evaluate_expected_terms,
    load_cases,
)


class EvaluateLocalTests(unittest.TestCase):
    def test_expected_terms_accepts_policy_paraphrases(self) -> None:
        queries, expected_outputs, _ = load_cases(DEFAULT_CASES_PATH)
        expected_by_query = dict(zip(queries, expected_outputs))
        cases = [
            (
                "What must a Contoso University researcher do before beginning "
                "research after receiving ethics conditions?",
                "Close all conditions and wait until the portal issues approval "
                "before starting. Source: "
                "contoso-research-ethics-approval-sop.md",
            ),
            (
                "Who must be involved when a Contoso University IT incident may "
                "be a personal data breach?",
                "Promptly escalate the incident to the Data Protection Office. "
                "Source: contoso-it-incident-response-sop.md",
            ),
            (
                "Who is allowed to issue a formal conditional employment offer "
                "at Contoso University?",
                "Only the Recruitment Team may issue the employment offer. "
                "Source: contoso-hr-staff-recruitment-sop.md",
            ),
        ]

        for query, response in cases:
            with self.subTest(response=response):
                result = evaluate_expected_terms(
                    response,
                    expected_by_query[query],
                )
                self.assertTrue(result["passed"], result["reason"])


if __name__ == "__main__":
    unittest.main()
