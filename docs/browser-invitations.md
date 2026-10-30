# Browser invitation guide

If you have been given an invitation code for the hosted trial, you can use
Argus without installing anything. The operator runs it on our own machines
(deployment files in [`deploy/`](../deploy/), service code in
`argus_trial_server/`); the address comes with your invitation. Use the address the
operator gives you, not one copied from an old message: temporary tunnel
addresses change when the service is restarted.

What the invitation gives you:

- **The Argus web workbench**, opened at `/invite` after you enter the
  code. Each invitation is its own workspace; the same code continues the same
  workspace from another device. A read-only option lets you browse without
  submitting or changing tasks.
- **A model already configured.** The server chooses the model and pays for it;
  you need no coding-agent CLI or account login.
- **A lifetime token allowance** shown on the entry page and at
  `/invite/status`. The code default is 10,000,000 input-plus-output tokens per
  invitation and it does not reset monthly; an operator may configure a
  different allowance (`token_limit` in `argus_trial_server/web_portal.py`).
- **A shared GPU queue** at `/invite/compute`, with 200 lifetime GPU-hours per
  invitation; time spent queued does not count
  ([hosted research trial](hosted-research-trial.md)).

How it differs from a local install:

- **Your work runs in an isolated container on the operator's host, not on your
  machine.** It cannot see your local files or private network; outbound access
  is HTTPS to public addresses only. Results are viewed and downloaded through
  the browser.
- **No terminal cockpit, desktop app, or choice of backend or model.** The terminal, desktop and backend/model choices in the
  [Argus guide](https://github.com/lbx154/Argus/blob/chore/hosting-out/docs/getting-started.md) apply to local installs only.
- **Data collection.** The entry page asks you to agree to a team-internal data
  notice before you start: task inputs, the four roles' process records, tool
  calls and results are recorded for internal training and product review.
  Research records are kept for up to 30 days, and the *研究记录与反馈* page lets
  you delete a project's research copy. Submit only material you may share on
  those terms.
- **Sessions and availability.** A browser sign-in lasts up to seven days.
  Invitations are issued privately by the operator; there is no public sign-up.
  An operator redeploy can interrupt work that is running in the foreground.

When you outgrow the trial (your own data, your own agent CLI, longer
campaigns), use the [local installation guide](https://github.com/lbx154/Argus/blob/chore/hosting-out/docs/getting-started.md). The entry page is in
Simplified Chinese; the workspace follows your browser language.

