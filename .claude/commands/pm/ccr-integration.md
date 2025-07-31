---
allowed-tools: Task, Write, Read, Edit, LS, Grep, Bash
---

# CCR Integration

Integrate Claude Code Router (CCR) with git workflows for intelligent code review routing.

## Usage
```
/pm:ccr-init          # Initialize CCR integration
/pm:ccr-submit        # Submit epic to CCR for review
/pm:ccr-status        # Check CCR review status
/pm:ccr-sync          # Sync CCR feedback to issues
```

## Configuration

In `.claude/config/ccr.json`:
```json
{
  "endpoint": "https://ccr.example.com",
  "webhook": "https://your-app.com/gh-webhook",
  "auto_mode": false,
  "review_required": true
}
```

## Commands

### `/pm:ccr-init`
Initialize CCR integration:
1. Check for existing `.claude/config/ccr.json`
2. Create sample config if missing
3. Test CCR connectivity
4. Set up webhook handler

### `/pm:ccr-submit [epic-name]`
Submit epic to CCR review:
1. Bundle epic changes into diff
2. Send to CCR router with metadata
3. Create CCR review request
4. Link CCR review ID to GitHub issue

### `/pm:ccr-status [epic-name]`
Check CCR review progress:
1. Query CCR API
2. Display review status
3. Show required approvals
4. List outstanding comments

### `/pm:ccr-sync [issue-id]`
Sync CCR feedback to GitHub:
1. Pull CCR review comments
2. Create/update GitHub issue comments
3. Apply CCR suggestions to code
4. Update epic status based on review

## Integration Points

### Epic Creation
- Epics auto-submitted to CCR if `auto_mode: true`
- CCR review required before merge if `review_required: true`

### Issue Completion
- CCR status checked before issue closure
- Merge blocked if CCR review pending

### Merge Request
- CCR comment integration in PR templates
- CCR status badge in epic documentation

## Sample Output

```
🤖 CCR Integration Ready

Endpoint: https://ccr.example.com
Webhook: https://your-app.com/gh-webhook
Auto-submit: enabled
Review required: true

Epic: user-auth
CCR Review ID: CCR-2024-001
Status: approved (2/2 approvals)
Last action: merged with suggestions
```