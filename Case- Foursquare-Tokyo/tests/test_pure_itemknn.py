from __future__ import annotations

import unittest

from crossrec_final.base import BaseAudit, BaseComponents, mix_base_components


class PureItemKNNTests(unittest.TestCase):
    def test_itemknn_only_has_no_hidden_popularity_tie_break(self):
        components = BaseComponents(
            collaborative={"u": {"popular": 0.0, "rare": 0.0}},
            content={"u": {"popular": 0.0, "rare": 0.0}},
            popularity={"u": {"popular": 1.0, "rare": 0.01}},
            audit=BaseAudit(
                model="toy",
                users=1,
                catalog_items=2,
                candidate_pairs=2,
                positive_itemknn_pairs=0,
                positive_content_pairs=0,
                popularity_fallback_pairs=2,
            ),
        )
        scores = mix_base_components(
            components,
            content_mix=0.0,
            mode="itemknn_only",
        )
        self.assertEqual(scores["u"]["popular"], 0.0)
        self.assertEqual(scores["u"]["rare"], 0.0)


if __name__ == "__main__":
    unittest.main()
