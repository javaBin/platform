# Team Lifecycle — Open Questions

## What works today

- **Member added**: Lambda adds to Google Group + GitHub team
- **Member removed**: Lambda removes from Google Group + GitHub team
- **Team created**: Lambda creates Google Group, GitHub team, AWS Budget
- **Description/budget changed**: Lambda updates in place

## What doesn't work yet

### Team deletion

Deleting a team YAML from the registry does nothing. The Lambda only processes files that exist — a deleted file can't be read. Orphaned resources remain:

- Google Group (`team-{name}@java.no`)
- GitHub team
- AWS Budget
- IAM role (once team-scoped roles are implemented)

### Repos after team deletion

Repos that were in the deleted GitHub team lose their team association. CI stops working because there's no IAM role to assume. The repos themselves are untouched.

## Decisions needed

1. **Should team deletion be destructive?** Auto-delete Google Group, GitHub team, budget, IAM role? Or archive/disable?
2. **Should deletion be blocked if the team still has repos?** Force teams to remove repos before deleting.
3. **How to detect deletions in CI?** The registry workflow could compare deleted files in the git diff and send a separate "delete" event to the Lambda.
4. **Grace period?** Should there be a cooldown before resources are actually removed?
