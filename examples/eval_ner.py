"""Example code that breaks on datasets>=4 ("Dataset scripts are no longer supported").

Run `unscript-datasets scan examples --offline examples/hub` to see a verdict for
every call below, and `unscript-datasets fix examples --offline examples/hub`
to see the replacement code.
"""
import datasets
from datasets import load_dataset
from datasets import load_dataset as ld

NAME_FROM_CONFIG = "eriktks/conll2003"

# A dataset whose owner has not converted it, but a conversion PR is open.
conll = load_dataset("eriktks/conll2003", split="validation", trust_remote_code=True)

# Same dataset by its old short name, through an alias, streaming kept as is.
conll_train = ld("conll2003", "conll2003", split="train", streaming=True)

# A hand-made fix that looks right but merges every config into "default".
conll_trap = load_dataset("eriktks/conll2003", "conll2003", split="validation", revision="refs/convert/parquet")

# Pinned to an old commit that still had the script; main was fixed since.
abalone = load_dataset("mstz/abalone", "binary", revision="56bbaf9671")

# Already Parquet on main.
glue = load_dataset("nyu-mll/glue", "mrpc", split="validation")

# A script-named file that is NOT the loading script (tasks_splits_and_features.py).
p3 = load_dataset("bigscience/P3", "adversarial_qa_dbert_answer_the_following_q", split="train")

# Gated dataset: cannot be judged without a token.
gaia = load_dataset("gaia-benchmark/GAIA", "2023_all", split="validation")

# Script dataset with no conversion at all: only its owner can fix it.
patho = datasets.load_dataset("MahmoodLab/Patho-Bench", trust_remote_code=True)

# Built-in builder: nothing to migrate.
local = load_dataset("json", data_files="data/train.json")

# Not a string literal: reported, never rewritten.
dynamic = load_dataset(NAME_FROM_CONFIG, split="test")
