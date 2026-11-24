"""Predictive modelling with leakage controls and honest evaluation.

What this module will and will not do
------------------------------------
It fits models to ask *which launch-time characteristics carry signal about
later activity*. It is a measurement instrument, not a trading tool: there are
no buy/sell outputs, no thresholds presented as decisions, and no claim that a
historical relationship will persist.

Four controls make the evaluation trustworthy:

1. **Leakage gate.** Predictors must be declared AT_LAUNCH in the feature
   registry. `assert_no_leakage` raises otherwise, so a forward-looking feature
   cannot reach a model even by accident.

2. **Time-aware split.** Pools are ordered by launch time; the model trains on
   earlier launches and is tested on later ones. A random split would let the
   model learn from launches that happened *after* the ones it is tested on —
   impossible in practice and a classic way to manufacture good scores.

3. **Determined outcomes only.** Pools whose outcome is undetermined (still
   active, younger than the horizon) are excluded rather than labelled False.

4. **A mandatory baseline.** Every model is compared against a majority-class
   baseline. Accuracy is *not* the headline metric, because these classes are
   imbalanced: ROC-AUC, average precision and balanced accuracy are reported,
   and accuracy is shown next to the baseline's accuracy so an unimpressive
   model cannot look impressive.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.pipeline.features import assert_no_leakage, model_input_features
from app.research.common import ResearchDataset

logger = logging.getLogger(__name__)

RANDOM_SEED = 20261124
MIN_TRAIN_ROWS = 30
MIN_TEST_ROWS = 10
#: minimum rows of the rarer class needed before a fit is attempted
MIN_MINORITY_CLASS = 5


def _numeric_design_matrix(
    frame: pd.DataFrame, features: list[str]
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Keep numeric predictors; one-hot the one categorical we model (dex)."""
    used: list[str] = []
    notes: list[str] = []
    out = pd.DataFrame(index=frame.index)

    for col in features:
        if col not in frame.columns:
            notes.append(f"{col}: not present in this dataset")
            continue
        if col == "dex":
            # Limit to the commonest venues; rare one-hot columns add variance
            # without information at these sample sizes.
            venues = frame["dex"].fillna("(unknown)").astype(str)
            top = venues.value_counts().head(5).index
            for venue in top:
                name = f"dex_{venue}"
                out[name] = (venues == venue).astype(int)
                used.append(name)
            notes.append(f"dex one-hot encoded over the {len(top)} commonest venues")
            continue
        series = pd.to_numeric(frame[col], errors="coerce")
        if series.notna().sum() == 0:
            notes.append(f"{col}: entirely missing, dropped")
            continue
        if series.nunique(dropna=True) <= 1:
            notes.append(f"{col}: constant in this sample, dropped")
            continue
        # Heavy right skew: log1p the non-negative monetary/count features so a
        # single extreme pool does not dominate a linear model.
        if col.endswith("_usd") and (series.dropna() >= 0).all():
            out[f"log1p_{col}"] = np.log1p(series)
            used.append(f"log1p_{col}")
            notes.append(f"{col}: log1p transformed (strong right skew)")
        else:
            out[col] = series
            used.append(col)
    return out, used, notes


def time_aware_split(
    frame: pd.DataFrame, test_fraction: float = 0.3
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Split by launch time: train on earlier launches, test on later ones."""
    if "pool_created_at" not in frame.columns:
        raise KeyError("pool_created_at is required for a time-aware split")

    work = frame.dropna(subset=["pool_created_at"]).sort_values("pool_created_at")
    n = len(work)
    if n == 0:
        return work, work, {"error": "no rows with a launch timestamp"}

    cut = max(1, int(n * (1 - test_fraction)))
    train, test = work.iloc[:cut], work.iloc[cut:]

    info = {
        "strategy": "time-aware (chronological) split by pool_created_at",
        "test_fraction_requested": test_fraction,
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "train_launch_window": [
            str(train["pool_created_at"].min()),
            str(train["pool_created_at"].max()),
        ]
        if len(train)
        else None,
        "test_launch_window": [
            str(test["pool_created_at"].min()),
            str(test["pool_created_at"].max()),
        ]
        if len(test)
        else None,
        "rationale": (
            "A random split would train on launches that occurred after those in the "
            "test set, which is impossible in practice and inflates scores."
        ),
    }
    # If every pool launched at nearly the same moment, the split is chronological
    # in name only. Say so rather than implying temporal generalisation.
    if len(train) and len(test):
        span = (work["pool_created_at"].max() - work["pool_created_at"].min()).total_seconds()
        if span < 3600:
            info["warning"] = (
                f"All launches fall within {span / 60:.0f} minutes, so the chronological "
                "split does not test generalisation across time in any meaningful sense."
            )
    return train, test, info


def classify_survival(
    dataset: ResearchDataset,
    *,
    target: str = "survived_24h",
    features: list[str] | None = None,
    test_fraction: float = 0.3,
) -> dict:
    """Fit a baseline, logistic regression and random forest for one outcome."""
    requested = features or [
        "initial_liquidity_usd",
        "initial_fdv_usd",
        "first_hour_volume_usd",
        "launch_hour_utc",
        "dex",
    ]
    # Hard gate: raises if any predictor is not known at launch.
    assert_no_leakage(requested)

    if target not in {"survived_24h", "survived_72h", "event_observed"}:
        raise ValueError(f"unsupported target {target!r}")

    frame = dataset.frame
    base_warnings = list(dataset.cohort_warnings)

    if frame.empty:
        return {
            "available": False,
            "message": "Dataset is empty.",
            "warnings": base_warnings,
        }

    work = frame.copy()
    # Three-valued outcome: drop undetermined rows rather than calling them False.
    raw_target = work[target]
    determined = raw_target.notna()
    undetermined_count = int((~determined).sum())
    work = work[determined].copy()
    work[target] = work[target].astype(bool)

    if undetermined_count:
        base_warnings.append(
            f"{undetermined_count} pool(s) excluded because their {target} outcome is "
            "undetermined (still active and younger than the horizon). Including them "
            "as failures would bias the model toward predicting failure."
        )

    design, used_features, prep_notes = _numeric_design_matrix(work, requested)
    if not used_features:
        return {
            "available": False,
            "message": (
                "No usable predictors. Legal launch-time inputs are: "
                + ", ".join(model_input_features())
            ),
            "warnings": base_warnings + prep_notes,
        }

    model_frame = pd.concat(
        [design, work[[target, "pool_created_at"]]], axis=1
    ).dropna(subset=[target, "pool_created_at"])

    if len(model_frame) < MIN_TRAIN_ROWS + MIN_TEST_ROWS:
        return {
            "available": False,
            "n": int(len(model_frame)),
            "n_excluded_undetermined": undetermined_count,
            "class_counts": {
                str(k): int(v) for k, v in model_frame[target].value_counts().items()
            },
            "message": (
                f"Only {len(model_frame)} pools have a determined {target} outcome and a "
                f"launch timestamp. At least {MIN_TRAIN_ROWS + MIN_TEST_ROWS} are needed "
                "to train and test. Collect more data before modelling."
            ),
            "warnings": base_warnings + prep_notes,
        }

    class_counts = model_frame[target].value_counts()
    if len(class_counts) < 2 or class_counts.min() < MIN_MINORITY_CLASS:
        return {
            "available": False,
            "n": int(len(model_frame)),
            "class_counts": {str(k): int(v) for k, v in class_counts.items()},
            "message": (
                f"Outcome {target} is too imbalanced to model: "
                + ", ".join(f"{k}={v}" for k, v in class_counts.items())
                + f". At least {MIN_MINORITY_CLASS} pools of the rarer class are required."
            ),
            "warnings": base_warnings + prep_notes,
        }

    train, test, split_info = time_aware_split(model_frame, test_fraction)
    if len(train) < MIN_TRAIN_ROWS or len(test) < MIN_TEST_ROWS:
        return {
            "available": False,
            "n": int(len(model_frame)),
            "message": (
                f"Chronological split leaves {len(train)} training and {len(test)} test "
                "rows, below the minimum."
            ),
            "warnings": base_warnings + prep_notes,
            "split": split_info,
        }

    x_train = train[used_features]
    y_train = train[target].astype(int).to_numpy()
    x_test = test[used_features]
    y_test = test[target].astype(int).to_numpy()

    if len(np.unique(y_train)) < 2:
        return {
            "available": False,
            "message": (
                "The training period contains only one outcome class, so no model can "
                "be fitted. This is a consequence of the chronological split on a "
                "short observation window."
            ),
            "warnings": base_warnings + prep_notes,
            "split": split_info,
        }

    results: list[dict] = []

    # 1. Mandatory baseline: always predict the training-set majority class.
    baseline = DummyClassifier(strategy="most_frequent")
    baseline.fit(x_train, y_train)
    results.append(
        _evaluate("baseline (majority class)", baseline, x_test, y_test, is_baseline=True)
    )

    # 2. Interpretable model. Imputation + scaling inside the pipeline so the
    #    imputer is fitted on training data only — fitting it on everything
    #    would leak test-set information through the imputed values.
    logistic = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=2000, random_state=RANDOM_SEED)),
        ]
    )
    logistic.fit(x_train, y_train)
    logistic_result = _evaluate("logistic regression", logistic, x_test, y_test)
    try:
        coefs = logistic.named_steps["model"].coef_[0]
        logistic_result["coefficients"] = [
            {
                "feature": f,
                "coefficient": float(c),
                "odds_ratio": float(np.exp(c)),
                "note": "per 1 SD of the (possibly log-transformed) predictor",
            }
            for f, c in sorted(
                zip(used_features, coefs, strict=False), key=lambda t: abs(t[1]), reverse=True
            )
        ]
    except Exception as exc:  # noqa: BLE001
        logistic_err = f"coefficients unavailable: {exc}"
        logistic_result["coefficients_error"] = logistic_err
    results.append(logistic_result)

    # 3. Comparison model. Shallow on purpose: with a few dozen rows a deep
    #    forest memorises the training set.
    forest = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=300,
                    max_depth=4,
                    min_samples_leaf=3,
                    random_state=RANDOM_SEED,
                    n_jobs=1,
                ),
            ),
        ]
    )
    forest.fit(x_train, y_train)
    forest_result = _evaluate("random forest (depth 4)", forest, x_test, y_test)
    try:
        importances = forest.named_steps["model"].feature_importances_
        forest_result["feature_importances"] = [
            {"feature": f, "importance": float(v)}
            for f, v in sorted(
                zip(used_features, importances, strict=False), key=lambda t: t[1], reverse=True
            )
        ]
        forest_result["importance_caveat"] = (
            "Impurity-based importances are biased toward high-cardinality features "
            "and do not imply causal influence."
        )
    except Exception as exc:  # noqa: BLE001
        forest_result["importances_error"] = str(exc)
    results.append(forest_result)

    baseline_acc = results[0]["accuracy"]
    best = max(
        (r for r in results if not r.get("is_baseline")),
        key=lambda r: (r["roc_auc"] if r["roc_auc"] is not None else -1),
        default=None,
    )

    verdict_warnings = list(base_warnings) + prep_notes
    if best and best["roc_auc"] is not None and best["roc_auc"] < 0.6:
        verdict_warnings.append(
            f"Best test ROC-AUC is {best['roc_auc']:.2f}, close to the 0.5 of random "
            "guessing. On this sample, launch-time features carry little signal about "
            f"{target}. Reporting this is the result — it is not a failure to hide."
        )
    if best and best["accuracy"] is not None and baseline_acc is not None:
        if best["accuracy"] <= baseline_acc:
            verdict_warnings.append(
                f"No model beat the majority-class baseline on accuracy "
                f"({baseline_acc:.2f}). Accuracy is the wrong headline metric for an "
                "imbalanced outcome; judge by ROC-AUC and average precision."
            )

    return {
        "available": True,
        "target": target,
        "target_definition": _target_definition(target, dataset),
        "n": int(len(model_frame)),
        "n_excluded_undetermined": undetermined_count,
        "class_counts": {str(k): int(v) for k, v in class_counts.items()},
        "features_requested": requested,
        "features_used": used_features,
        "preprocessing_notes": prep_notes,
        "split": split_info,
        "random_seed": RANDOM_SEED,
        "models": results,
        "warnings": verdict_warnings,
        "reproducibility": {
            "seed": RANDOM_SEED,
            "dataset_version_id": dataset.version_id,
            "dataset_label": dataset.label,
            "note": (
                "Re-running with the same dataset version and seed reproduces these "
                "numbers exactly."
            ),
        },
    }


def _evaluate(
    name: str, model, x_test: pd.DataFrame, y_test: np.ndarray, *, is_baseline: bool = False
) -> dict:
    """Compute test metrics, choosing metrics appropriate to imbalanced classes."""
    pred = model.predict(x_test)
    try:
        proba = model.predict_proba(x_test)[:, 1]
    except (AttributeError, IndexError):
        proba = None

    single_class = len(np.unique(y_test)) < 2
    roc = ap = None
    if proba is not None and not single_class:
        roc = float(roc_auc_score(y_test, proba))
        ap = float(average_precision_score(y_test, proba))

    tn = fp = fn = tp = None
    try:
        cm = confusion_matrix(y_test, pred, labels=[0, 1])
        tn, fp, fn, tp = (int(v) for v in cm.ravel())
    except Exception:  # noqa: BLE001
        pass

    out = {
        "model": name,
        "is_baseline": is_baseline,
        "n_test": int(len(y_test)),
        "accuracy": float((pred == y_test).mean()),
        "balanced_accuracy": float(balanced_accuracy_score(y_test, pred)),
        "roc_auc": roc,
        "average_precision": ap,
        "brier_score": float(brier_score_loss(y_test, proba)) if proba is not None else None,
        "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "metric_notes": [],
    }
    if single_class:
        out["metric_notes"].append(
            "The test period contains a single outcome class, so ROC-AUC and average "
            "precision are undefined."
        )
    if roc is not None:
        out["metric_notes"].append(
            "ROC-AUC is the probability the model ranks a randomly chosen surviving "
            "pool above a randomly chosen non-surviving one. 0.5 is random."
        )
    return out


def _target_definition(target: str, dataset: ResearchDataset) -> str:
    threshold = dataset.inactivity_threshold_hours
    if target == "survived_24h":
        return (
            "True if measurable trading activity continued at least 24h after pool "
            f"creation; False if activity stopped (for more than {threshold}h) before "
            "24h. Pools still active and younger than 24h are excluded as undetermined."
        )
    if target == "survived_72h":
        return (
            "True if measurable trading activity continued at least 72h after pool "
            f"creation; False if it stopped (for more than {threshold}h) before 72h. "
            "Undetermined pools excluded."
        )
    return (
        f"True if the pool was observed to go inactive for more than {threshold}h "
        "(the event), False if right-censored."
    )
