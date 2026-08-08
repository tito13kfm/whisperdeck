# Wrong Directions Encountered

## 1. Issue body file path error
**Issue stated**: `index.html:7-9` and `templates/index.html`
**Actual location**: `static/index.html`
**Impact**: Minor. The issue's line numbers were correct for the actual file, but the path was wrong. No time wasted since I verified the actual file location during investigation.
**Recommendation**: Issue body should be updated to reflect correct path `static/index.html`.

## 2. Issue body incomplete @font-face count
**Issue stated**: Showed one @font-face example with comment "repeat for each family/weight"
**Actual requirement**: 8 @font-face declarations (not explicitly stated)
**Impact**: None. I correctly identified all 8 font/weight combinations from the Google Fonts CSS URL.
**Recommendation**: Issue body could be more explicit about the exact count of @font-face declarations needed.

## 3. Issue body missed existing CSS variables
**Issue stated**: Suggested adding font-family declarations to rack.css
**Actual state**: CSS variables already defined (lines 61-64), just needed @font-face declarations
**Impact**: None. I correctly identified that the CSS variables were already in place and only @font-face declarations were needed.
**Recommendation**: Issue body should mention that CSS variables already exist and only @font-face declarations are needed.

## 4. Explore agents took 3-4 minutes each
**Issue**: Local Lemonade agents are slow for simple file reads
**Impact**: Moderate. Two explore agents took 3m10s and 4m48s respectively to find and read two files.
**Recommendation**: For simple file reads, use direct `read` tool instead of explore agents. Reserve explore agents for complex multi-file searches or pattern discovery.

## 5. AGENTS.md local/cloud agent labeling
**Issue**: AGENTS.md line 127 lists `atlas`, `quick`, `writing`, and `unspecified-low` as OpenRouter-only, but they're actually mapped to local Lemonade models
**Impact**: None for this task (I didn't use those categories).
**Recommendation**: AGENTS.md should be updated to reflect current config. The doc error is already noted in AGENTS.md itself.
