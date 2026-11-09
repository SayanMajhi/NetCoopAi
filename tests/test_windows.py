"""B3 window calculations: gaps and counter resets must not become signals."""

import unittest

from netcoop.normalizer.windows import counter_deltas


class WindowTests(unittest.TestCase):
    def test_counter_reset_is_not_a_negative_error_delta(self):
        samples = [10, 15, 3, 5]
        self.assertEqual(([5.0, None, 2.0], [2]), counter_deltas(samples))
        self.assertEqual([10, 15, 3, 5], samples)
        self.assertEqual(([0.0, None, None, 2.0], []), counter_deltas([0, 0, None, 4, 6]))
        self.assertEqual(([], []), counter_deltas([5]))
        self.assertEqual(([], []), counter_deltas([]))


if __name__ == "__main__":
    unittest.main()
