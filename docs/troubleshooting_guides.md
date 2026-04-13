# AI Service Deployment — Troubleshooting Manual

**Server environment**: Ubuntu 22.04, NVIDIA GeForce RTX 3050 (sm_86), Python 3.10.13 via Conda (`/opt/conda`), PyTorch 2.2.1+CUDA pre-installed.

> **Golden rule**: This server uses a **conda-managed Python environment**. Conda and pip fight over package ownership. Never use `pip install --upgrade` globally — it risks replacing the GPU torch with a CPU-only wheel.

---

## Error 1 — LoRA Adapter Not Loading (`corda_config` TypeError)

### Symptom
```
⚠️  LoRA adapter incompatible (PEFT version mismatch): LoraConfig.__init__() got an unexpected keyword argument 'corda_config'
   Using base model only.
```

### Root Cause
The adapter `trunghieu1206/lawchatbot-40k` was saved with PEFT ≥ 0.15.0 which introduced `corda_config` (CorDA LoRA initialization). The server had `peft==0.14.x` installed which does not know that key → `TypeError` → silent fallback to base model (embeddings less accurate).

### Fix — `requirements.txt`
```
# MUST be >=0.15.0: corda_config was introduced in PEFT 0.15.0
# MUST be >=0.18.0: fixes broken EncoderDecoderCache import (see Error 2)
peft>=0.18.0
```

### Fix — `main.py` (self-healing loader)
Replace `PeftModel.from_pretrained()` with a two-pass loader (`_load_peft_with_compat`):
1. Try normal load.
2. On `TypeError: unexpected keyword argument`, download `adapter_config.json` from HuggingFace, introspect `LoraConfig.__init__` signature, strip unknown keys, retry from patched local copy.
3. Only fall back to base model if the retry also fails.

See: `ai-service/app/main.py` — function `_load_peft_with_compat()`.

---

## Error 2 — PEFT Cannot Import `EncoderDecoderCache`

### Symptom
```
ImportError: cannot import name 'EncoderDecoderCache' from 'transformers'
```
Appears during `from peft import PeftModel`.

### Root Cause
PEFT 0.15.x imports `EncoderDecoderCache` from `transformers`. That class was **removed in transformers ≥ 4.47**. The conda server had a new transformers installed alongside old PEFT → incompatible import at startup.

### Fix — `requirements.txt`
```
transformers>=4.44.0
peft>=0.18.0   # 0.18.x removed the broken EncoderDecoderCache import
```

### Key Compatibility Matrix
| peft version | transformers requirement |
|---|---|
| 0.14.x | transformers ~4.40-4.42 |
| 0.15.x | transformers ≥4.43, <4.47 |
| **0.18.x** | **transformers ≥4.44 (any recent)** ✅ |

---

## Error 3 — `torch.load` Security Block (CVE-2025-32434)

### Symptom
```
ValueError: Due to a serious vulnerability issue in `torch.load`, even with
`weights_only=True`, we now require users to upgrade torch to at least v2.6
in order to use the function. This version restriction does not apply when
loading files with safetensors.
See: https://nvd.nist.gov/vuln/detail/CVE-2025-32434
```
Appears in `AutoModel.from_pretrained()`.

### Root Cause
`transformers ≥ 4.57` added a hard block on `torch.load` when `torch < 2.6` to patch CVE-2025-32434. The server runs `torch 2.2.1` (GPU conda install) which is below 2.6.

The error message itself gives the solution: *"This version restriction does not apply when loading files with safetensors."*

### Fix — `main.py`
```python
base_model = AutoModel.from_pretrained(
    base_model_name,
    trust_remote_code=True,
    use_safetensors=True,   # ← loads .safetensors file, bypasses torch.load entirely
)
```
`BAAI/bge-m3` ships `model.safetensors` so this is always safe. Safetensors is also faster and more memory-efficient than pickle-based `.bin` files.

---

## Error 4 — `sentence-transformers 3.x` Disables PyTorch

### Symptom
```
Disabling PyTorch because PyTorch >= 2.4 is required but found 2.2.1
PyTorch was not found. Models won't be available and only tokenizers,
configuration and file/data utilities can be used.
```
Then `AutoModel.from_pretrained` fails with `ImportError: AutoModel requires the PyTorch library`.

### Root Cause
`sentence-transformers ≥ 3.0.0` requires `torch ≥ 2.4`. When it finds `torch 2.2.1`, it executes internal code that **sets `transformers._torch_available = False`**, making `AutoModel` believe torch doesn't exist — even though the GPU probe passes and torch is working fine.

The trigger: running `pip install --upgrade` (added in a previous attempt to fix Error 2) caused pip to upgrade `sentence-transformers` to 3.x as a side effect.

### Fix — `requirements.txt`
```
# sentence-transformers 3.x requires torch>=2.4 and patches
# transformers._torch_available=False when it finds an older torch.
# Server runs torch 2.2.1 (GPU conda install) — must stay on 2.x.
sentence-transformers>=2.6.0,<3.0.0
```

### Fix — `deploy_nodocker.sh`
**Remove `--upgrade` from the mass pip install.** Without `--upgrade`, pip only installs/upgrades packages that don't already satisfy version constraints — torch is never touched.
```bash
# WRONG — risks replacing GPU conda torch with CPU pip wheel:
pip install --upgrade -r requirements_notorch.txt

# CORRECT — only installs what is missing or below constraint floor:
pip install -r requirements_notorch.txt
```

---

## Error 5 — `No module named 'pkg_resources'` (milvus-lite)

### Symptom
```
File ".../milvus_lite/__init__.py", line 15, in <module>
    from pkg_resources import DistributionNotFound, get_distribution
ModuleNotFoundError: No module named 'pkg_resources'

pymilvus.exceptions.ConnectionConfigException: milvus-lite is required for
local database connections. Please install it with: pip install pymilvus[milvus_lite]
```

### Root Cause (Multi-layer)
`milvus-lite < 2.4.9` uses `from pkg_resources import ...` in its `__init__.py` just to read its own version string. `pkg_resources` is provided by `setuptools`.

**Why pip upgrades don't fix it**: The server's milvus-lite is installed by **conda**, not pip. In this conda environment, pip can "install" a newer milvus-lite but the **conda-managed file on disk is never actually replaced**. Every pip upgrade attempt silently succeeded without changing the file at `/opt/conda/lib/python3.10/site-packages/milvus_lite/__init__.py`.

### Fix — `main.py` (definitive, conda-proof)
Inject a `pkg_resources` stub into `sys.modules` **before any pymilvus import**. This is placed at the very top of `main.py`, right after `os.environ.pop("MILVUS_URI", None)`:

```python
try:
    import pkg_resources  # fine if available
except ModuleNotFoundError:
    import sys as _sys
    import types as _types

    class _PkgResources(_types.ModuleType):
        class DistributionNotFound(Exception):
            pass

        @staticmethod
        def get_distribution(name: str):
            # IMPORTANT: import locally — do NOT reference outer-scope variables
            # that are cleaned up by `del` after the stub is registered.
            import importlib.metadata as _m
            try:
                dist = _m.distribution(name)
                class _Dist:
                    version = dist.metadata["Version"]
                return _Dist()
            except _m.PackageNotFoundError:
                raise _PkgResources.DistributionNotFound(name)

    _stub = _PkgResources("pkg_resources")
    _stub.DistributionNotFound = _PkgResources.DistributionNotFound
    _stub.get_distribution = _PkgResources.get_distribution
    _sys.modules["pkg_resources"] = _stub
    del _sys, _types, _stub, _PkgResources  # keep namespace clean
```

> ⚠️ **Critical scoping rule**: The `get_distribution` method must use a **local import** for `importlib.metadata`. If you reference a module-level `_imeta` variable that is later deleted by `del`, calling `get_distribution` at runtime will raise `NameError: name '_imeta' is not defined`.

### Fix — `requirements.txt` (future-proof)
```
# milvus-lite <2.4.9 uses pkg_resources; >=2.4.9 uses importlib.metadata.
milvus-lite>=2.4.9
```

### Fix — `deploy_nodocker.sh` (belt-and-suspenders)
```bash
# Install setuptools first (provides pkg_resources as fallback)
"$AI_PYTHON" -m pip install "setuptools>=70.0" --quiet || true

# After main requirements, force-upgrade milvus-lite + pymilvus
# (safe: neither declares torch as a dependency, so GPU torch is untouched)
"$AI_PYTHON" -m pip install "milvus-lite>=2.4.9" "pymilvus>=2.4.0" --upgrade --quiet || true
```

---

## Dependency Version Reference (Working State)

Confirmed working on **2026-04-13** with this server environment:

| Package | Pinned Version | Reason |
|---|---|---|
| `torch` | `2.2.1+cu121` | Pre-installed GPU conda — **never pip upgrade** |
| `transformers` | `>=4.44.0` (resolved: 4.57.6) | Compatible with peft 0.18.x |
| `peft` | `>=0.18.0` (resolved: 0.18.1) | Supports corda_config + no broken EncoderDecoderCache import |
| `sentence-transformers` | `>=2.6.0,<3.0.0` | 3.x requires torch>=2.4 and breaks transformers |
| `milvus-lite` | `>=2.4.9` | Older versions use deprecated pkg_resources |
| `setuptools` | `>=70.0` | Provides pkg_resources |

### Working `requirements.txt`
```
fastapi==0.111.0
uvicorn[standard]==0.30.1
pydantic==2.7.1
python-dotenv==1.0.1
torch>=2.1.0
sentence-transformers>=2.6.0,<3.0.0
langchain>=0.2.0
langchain-core>=0.2.0
langchain-openai>=0.1.7
langchain-milvus>=0.1.0
langgraph>=0.1.0
huggingface_hub>=0.23.0
pymilvus>=2.4.0
milvus-lite>=2.4.9
psycopg2-binary>=2.9.9
sqlalchemy>=2.0.0
transformers>=4.44.0
accelerate>=0.30.0
peft>=0.18.0
setuptools>=70.0
```

---

## General Rules for This Server

1. **Never `pip install --upgrade` globally.** Always filter out `torch` and only upgrade with targeted commands for specific packages.
2. **Conda owns some packages** (torch, milvus-lite, etc.). pip "upgrades" may succeed without changing disk files. Code-side fixes (shims, `use_safetensors=True`) are more reliable than pip-side fixes.
3. **Always verify torch CUDA** after any pip install session: `/opt/conda/bin/python3 -c "import torch; print(torch.cuda.is_available())"` — must return `True`.
4. **The AI service startup takes 3–5 minutes** (loading BGE-M3 + LoRA adapter merge). The 15s health check in deploy will always warn — wait and check the log manually.
5. **Check versions in the log** — the deploy script now prints: `✅ All imports OK | torch=X | peft=X | transformers=X`. Use this to diagnose version mismatches instantly.
