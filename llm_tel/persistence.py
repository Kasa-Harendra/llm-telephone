import csv, json, pathlib, datetime as dt

def mk_exp_dir(seed: str) -> pathlib.Path:
    slug = "_".join(seed.lower().split()[:3])
    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    path = pathlib.Path("experiments") / f"{slug}_{ts}"
    path.mkdir(parents=True, exist_ok=True)
    return path

class Recorder:
    def __init__(self, exp_dir: pathlib.Path):
        self.csv = open(exp_dir / "chain.csv", "w", newline="")
        self.jl = open(exp_dir / "chain.jsonl", "w")
        self.w = csv.DictWriter(self.csv, fieldnames=["cycle", "phase", "model", "text", "score"])
        self.w.writeheader()

    def log(self, **row):
        self.w.writerow(row)
        self.jl.write(json.dumps(row) + "\n")
        self.jl.flush()
