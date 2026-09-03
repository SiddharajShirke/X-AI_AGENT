# Demo script

Run:

```powershell
.\.venv\Scripts\python.exe scripts\demo_flow.py
```

The credential-free script creates two X account workspaces, assigns different domains, rejects and regenerates under Account 1, verifies Account 2 received none of Account 1's feedback, and explicitly approves one dry-run draft for each account.

For a browser demonstration:

1. Start the app and open **Accounts**.
2. Add a second account using **Copy settings from**.
3. Open each account and show separate review/history data.
4. Use Setup to bind different Buffer channel IDs and Slack channels, or reuse the same saved connections.
5. Generate and reject a draft under Account 1; confirm Account 2 learning is unchanged.
6. Approve a replacement in dry-run mode.
7. After a test Slack connection is configured, demonstrate signed Slack reject and approval actions.

Do not enable live Buffer posting until a disposable test X channel and exact final text have been verified.
