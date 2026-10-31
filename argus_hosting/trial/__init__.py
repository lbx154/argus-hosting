"""The hosted client; configure it with ``argus-trial``.

The service implementation is argus_trial_server in the same distribution.
"""

MODEL = "argus-trial"
# The client selector is not evidence of the actual upstream model.
CLIENT_MODEL = MODEL
REASONING_EFFORT = "high"
MAX_OUTPUT_TOKENS = 16_384
TRIAL_URL = "https://argusbot.cn"
