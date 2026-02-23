Create a pull request from the current branch to main.

1. Run `git status` to check for uncommitted changes — if there are any, warn the user and ask if they want to commit first
2. Run `git log main..HEAD --oneline` to see all commits on this branch
3. Run `git diff main...HEAD --stat` to see all files changed
4. Analyze the commits and changes to generate:
   - A concise PR title (under 70 chars)
   - A summary with bullet points explaining the changes
   - A test plan checklist
5. Push the branch if not already pushed: `git push -u origin <branch>`
6. Create the PR using `gh pr create` with the generated title and body
7. Return the PR URL
