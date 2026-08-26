import unittest

from intake_api.search_projection import build_search_projection


class SearchProjectionTests(unittest.TestCase):
    def test_projection_is_deterministic_and_flattens_nested_values(self) -> None:
        intake = {
            "title": "  Improve onboarding  ",
            "requester": {
                "name": "Alex",
                "email": "alex@example.test",
            },
            "tags": ["students", "automation"],
            "enabled": True,
            "ignored": None,
        }

        title, text = build_search_projection(intake)

        self.assertEqual("Improve onboarding", title)
        self.assertEqual(
            [
                "enabled: true",
                "requester.email: alex@example.test",
                "requester.name: Alex",
                "tags: students",
                "tags: automation",
                "title: Improve onboarding",
            ],
            text.splitlines(),
        )

    def test_projection_ignores_empty_and_unsupported_values(self) -> None:
        title, text = build_search_projection(
            {
                "title": " ",
                "empty": "",
                "object": object(),
                "count": 3,
            }
        )

        self.assertEqual("", title)
        self.assertEqual("count: 3", text)


if __name__ == "__main__":
    unittest.main()
