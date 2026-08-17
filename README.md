# demo-support synthetic evaluator (Python)

Set `LD_EVALUATION_SDK_KEY` and `DEMO_ENVIRONMENT`, then `python app.py --profile staging`. Each batch opens a client, evaluates every flag the release owns, flushes and closes.
