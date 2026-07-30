package sacm

default decision = {
  "allow": false,
  "reason": "Action is not allowed by the local default policy.",
}

decision = {
  "allow": true,
  "reason": "Sandbox execution is permitted.",
} {
  input.action == "workspace.execute"
}

decision = {
  "allow": true,
  "reason": "Draft pull request requires an approval.",
  "requires_approval": true,
} {
  input.action == "github.create_draft_pr"
}
