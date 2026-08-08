import unittest

from coreytrading import MarketSnapshot, Regime, TechnicalScoreModel


class TechnicalScoreModelTest(unittest.TestCase):
    def test_aligned_snapshot_scores_above_neutral(self) -> None:
        snapshot = MarketSnapshot(
            symbol="SYNTH_A",
            close=105.0,
            moving_average=100.0,
            atr=2.5,
            volume_ratio=1.4,
            regime=Regime.BULL,
        )
        model = TechnicalScoreModel()
        prediction = model.predict(model.features(snapshot))
        self.assertGreater(prediction.score, 60)
        self.assertIn(prediction.label, {"positive", "strong"})

    def test_score_is_bounded(self) -> None:
        snapshot = MarketSnapshot(
            symbol="SYNTH_B",
            close=10.0,
            moving_average=100.0,
            atr=0.1,
            volume_ratio=0.0,
            regime=Regime.BEAR,
        )
        model = TechnicalScoreModel()
        prediction = model.predict(model.features(snapshot))
        self.assertGreaterEqual(prediction.score, 0)
        self.assertLessEqual(prediction.score, 100)


if __name__ == "__main__":
    unittest.main()
