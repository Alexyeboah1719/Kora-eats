import os
os.environ["BACKGROUND_JOBS"] = "false"  # never start background loops during tests
os.environ["API_RATE_LIMIT_PER_MINUTE"] = "100000"
