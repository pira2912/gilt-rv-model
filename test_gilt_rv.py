import unittest

import pandas as pd

from gilt_rv import add_features, largest_irregularities


class CurveModelTests(unittest.TestCase):
    def test_spread_and_outlier_order(self):
        index = pd.date_range("2020-01-01", periods=5)
        curves = pd.DataFrame(
            {
                "y2": [1, 1, 1, 1, 1],
                "y5": [2, 2.1, 1.9, 2.05, 3],
                "y10": [3, 3, 3, 3, 3],
                "y30": [4, 4, 4, 4, 4],
            },
            index=index,
        )
        data = add_features(curves, lookback=2)
        self.assertEqual(data.loc[index[-1], "2s5s_spread"], 200)
        self.assertEqual(len(largest_irregularities(data, limit=3)), 3)


if __name__ == "__main__":
    unittest.main()
