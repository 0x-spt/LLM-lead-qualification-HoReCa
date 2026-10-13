"""Metrics for the binary "accept / reject" decision, with a Wilson confidence interval."""
import math


def _div(a, b):
    return a / b if b else None


def wilson(k, n, z=1.96):
    """A 95% interval for the proportion k/n — with only a few hundred examples, a bare point
    estimate with no interval is misleading."""
    if not n:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def binary(gold, pred, positive):
    tp = sum(1 for g, p in zip(gold, pred) if g == positive and p == positive)
    fp = sum(1 for g, p in zip(gold, pred) if g != positive and p == positive)
    fn = sum(1 for g, p in zip(gold, pred) if g == positive and p != positive)
    tn = sum(1 for g, p in zip(gold, pred) if g != positive and p != positive)
    precision, recall = _div(tp, tp + fp), _div(tp, tp + fn)
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
    n = len(gold)
    return {'n': n, 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'precision': precision, 'recall': recall, 'f1': f1, 'accuracy': _div(tp + tn, n),
            'precision_ci': wilson(tp, tp + fp), 'recall_ci': wilson(tp, tp + fn)}
