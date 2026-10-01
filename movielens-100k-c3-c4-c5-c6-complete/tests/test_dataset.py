from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from full_c5_recommend.dataset import BuildOptions, GENRES, build_case


def movie_row(movie_id: str, title: str, enabled: set[str]) -> str:
    flags = ["1" if genre in enabled else "0" for genre in GENRES]
    return "|".join([movie_id, title, "01-Jan-1995", "", "http://example"] + flags)


class MovieLensDatasetTests(unittest.TestCase):
    def test_temporal_graph_and_content_only_ii_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movies = root / "u.item"
            ratings = root / "u.data"
            movies.write_text(
                "\n".join(
                    [
                        movie_row("1", "Action One (1995)", {"Action", "Adventure"}),
                        movie_row("2", "Action Two (1996)", {"Action", "Adventure"}),
                        movie_row("3", "Action Three (1997)", {"Action", "Thriller"}),
                        movie_row("4", "Comedy One (1995)", {"Comedy"}),
                    ]
                )
                + "\n",
                encoding="latin-1",
            )
            ratings.write_text(
                "\n".join(
                    [
                        "1\t1\t5\t10",
                        "1\t2\t5\t20",
                        "2\t2\t4\t30",
                        "2\t3\t4\t40",
                        "1\t3\t5\t120",
                        "1\t1\t5\t130",
                    ]
                )
                + "\n",
                encoding="ascii",
            )
            case = build_case(
                ratings_path=ratings,
                movies_path=movies,
                output_dir=root / "case",
                options=BuildOptions(
                    fixed_cutoff=100,
                    ii_top_k=3,
                    genre_weight=0.8,
                    year_weight=0.2,
                    ii_similarity_threshold=0.4,
                ),
            )
            self.assertFalse(case.metadata["ratings_used_for_ii_edges"])
            self.assertEqual(len(case.truth), 1)
            self.assertEqual(case.nodes[case.node_to_id[case.truth[0][1]]].entity_id, "3")
            ii_reasons = [
                record.reason for record in case.edge_records if record.edge_type == "II"
            ]
            self.assertTrue(ii_reasons)
            self.assertTrue(all("genre_cosine" in reason for reason in ii_reasons))
            self.assertTrue(all("genre_weight=0.800000" in reason for reason in ii_reasons))
            self.assertTrue(all("year_weight=0.200000" in reason for reason in ii_reasons))
            self.assertTrue(all("threshold_gt=0.400000" in reason for reason in ii_reasons))

    def test_similarity_threshold_is_strict_and_weights_are_configurable(self) -> None:
        # One shared genre out of two-vs-two gives cosine 0.5. If both years
        # were missing, 0.8*0.5 = exactly 0.4 and must NOT create an edge
        # because the requested predicate is score > 0.4, not >= 0.4.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movies = root / "u.item"
            ratings = root / "u.data"
            movies.write_text(
                "\n".join(
                    [
                        movie_row("1", "First", {"Action", "Adventure"}),
                        movie_row("2", "Second", {"Action", "Thriller"}),
                    ]
                ) + "\n",
                encoding="latin-1",
            )
            # Strip the release date so the year term is exactly zero.
            rows = movies.read_text(encoding="latin-1").splitlines()
            fixed = []
            for row in rows:
                cols = row.split("|")
                cols[2] = ""
                fixed.append("|".join(cols))
            movies.write_text("\n".join(fixed) + "\n", encoding="latin-1")
            ratings.write_text(
                "1\t1\t5\t10\n2\t2\t5\t20\n1\t2\t5\t120\n",
                encoding="ascii",
            )
            case = build_case(
                ratings_path=ratings,
                movies_path=movies,
                output_dir=root / "case",
                options=BuildOptions(
                    fixed_cutoff=100,
                    ii_top_k=20,
                    genre_weight=0.8,
                    year_weight=0.2,
                    ii_similarity_threshold=0.4,
                ),
            )
            self.assertEqual(
                sum(record.edge_type == "II" for record in case.edge_records),
                0,
            )


if __name__ == "__main__":
    unittest.main()
