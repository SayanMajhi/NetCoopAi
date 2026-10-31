"""B3 window calculations: gaps and counter resets must not become signals."""

import unittest
from math import inf, nan

from netcoop.normalizer.windows import counter_deltas, median, p95, sustained


class WindowTests(unittest.TestCase):
    def test_median_and_p95_ignore_missing_without_inventing_zero(self):
        self.assertEqual(3.0, median([1, None, 3, 5]))
        self.assertEqual(4.0, median([1, 3, 5, 7]))
        self.assertEqual(5.0, p95([1, None, 2, 3, 4, 5]))
        self.assertEqual(19.0, p95(range(1, 21)))  # nearest rank: ceil(.95 * 20)
        self.assertIsNone(median([None, nan, inf]))
        self.assertIsNone(p95([]))

    def test_sustained_needs_consecutive_values_strictly_above(self):
        self.assertTrue(sustained([80, 91, 92, 93], 90, 3))
        self.assertFalse(sustained([91, 92, None, 93], 90, 3))
        self.assertFalse(sustained([91, 90, 92, 93], 90, 3))
        self.assertFalse(sustained([91, nan, 92, 93], 90, 3))
        self.assertTrue(sustained([0], -1, 1))
        with self.assertRaises(ValueError):
            sustained([1], 0, 0)

    def test_counter_reset_is_not_a_negative_error_delta(self):
        samples = [10, 15, 3, 5]
        self.assertEqual(([5.0, None, 2.0], [2]), counter_deltas(samples))
        self.assertEqual([10, 15, 3, 5], samples)
        self.assertEqual(([0.0, None, None, 2.0], []), counter_deltas([0, 0, None, 4, 6]))
        self.assertEqual(([], []), counter_deltas([5]))
        self.assertEqual(([], []), counter_deltas([]))


if __name__ == "__main__":
    unittest.main()
