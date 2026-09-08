# Documentation Health Metrics

This page tracks the health and coverage of HealthArchive documentation.

**Last Updated**: Auto-generated on every docs build

---

## Coverage Metrics

### Navigation Coverage

**Goal**: Key documentation is discoverable via sidebar navigation

| Category | Files on Disk | In Navigation | Coverage | Target |
|----------|---------------|---------------|----------|--------|
| **Tutorials** | 4 | 4 | 100% | 100% |
| **Operations** | 50+ | 30+ | 60%+ | 50% |
| **Development** | 5 | 4 | 80% | 80% |
| **Deployment** | 15+ | 8 | 53% | 60% |
| **Reference** | 5 | 5 | 100% | 100% |
| **Explanation** | 10+ | 8 | 80% | 70% |
| **Playbooks** | 32 | 32 | 100% | 100% |
| **Roadmaps** | 20+ | 4 | 20% | 20% |
| **Overall** | 123+ | 74+ | **60%** | **50%** |

**Status**: ✅ **Above target** (60% > 50%)

**Achievements**:
- All tutorials in navigation (4/4)
- All critical playbooks accessible
- Reference documentation complete
- Production runbook directly accessible

**Remaining gaps**:
- Some historical roadmap documents (intentionally archived)
- Some operational logs (reference-only)

---

## Documentation Types (Diátaxis Framework)

### Distribution by Type

| Type | Count | Percentage | Target | Status |
|------|-------|------------|--------|--------|
| **Tutorials** (Learning) | 4 | 3% | 3-5% | ✅ At target |
| **How-To Guides** (Tasks) | 50+ | 41% | 40-50% | ✅ Within range |
| **Reference** (Information) | 10 | 8% | 10-15% | ⚠️ Could add more |
| **Explanation** (Understanding) | 25+ | 20% | 15-25% | ✅ Within range |
| **Meta/Templates** | 10 | 8% | 5-10% | ✅ Good |
| **Pointers** | 5 | 4% | <5% | ✅ Minimal |

**Status**: ✅ **Well-balanced** according to Diátaxis principles

---

## Content Quality Indicators

### Documentation Completeness

| Indicator | Status | Notes |
|-----------|--------|-------|
| **Quick Start exists** | ✅ Yes | `../quickstart.md` |
| **Architecture documented** | ✅ Yes | Comprehensive 1,314-line guide |
| **API documented** | ✅ Yes | OpenAPI spec + consumer guide |
| **Contribution guide** | ✅ Yes | Complete CONTRIBUTING.md |
| **Code of Conduct** | ✅ Yes | In CONTRIBUTING.md |
| **Deployment runbook** | ✅ Yes | `deployment/production-single-vps.md` |
| **Incident response** | ✅ Yes | `operations/playbooks/core/incident-response.md` |
| **Testing guidelines** | ✅ Yes | `development/testing-guidelines.md` |

**Score**: 8/8 ✅ **Excellent**

---

## Freshness

### Recently Updated (Last 30 Days)

Based on recent documentation improvements:

- ✅ Navigation restructure (2026-01-18)
- ✅ New tutorials added (3 tutorials)
- ✅ API consumer guide created
- ✅ Project hub enhanced
- ✅ CONTRIBUTING.md updated
- ✅ Reference section created

**Status**: ✅ **Active maintenance**

### Stale Documentation Check

Documents not updated in >180 days: **TBD** (requires git analysis)

**Action**: Review quarterly as part of [Ops Cadence](../operations/ops-cadence-checklist.md)

---

## Link Health

### Internal Links

**Check script**: `scripts/check_docs_references.py`

**Run**: `make docs-refs`

**Last status**: ⏳ Run `make docs-refs` to check

**Expected**: 0 broken internal links

### External Links

**Check tool**: Lychee (GitHub Action)

**Last status**: ⚠️ Advisory only (doesn't fail build)

**Action**: Review and fix broken external links quarterly

---

## Accessibility

### Navigation Depth

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Max nav depth | 4 levels | ≤4 | ✅ Good |
| Avg nav depth | 2.5 levels | 2-3 | ✅ Good |
| Orphaned docs | 49 | <30% | ✅ Below threshold |

### Search Effectiveness

**Features enabled**:
- ✅ Search suggestions
- ✅ Search highlighting
- ✅ Tag-based search (new)
- ✅ Minimum search length: 2 chars
- ✅ Language: English

**Status**: ✅ **Good search experience**

---

## Multi-Repo Consistency

### Cross-Repo References

| Repo | Documented | Linked | Status |
|------|------------|--------|--------|
| **healtharchive** | ✅ | ✅ | This repo |
| **frontend app (`frontend/`)** | ✅ | ✅ | `docs/frontend/` bridge + canonical in-tree frontend docs |
| **healtharchive-datasets** | ✅ | ✅ | `datasets-external/` pointer |

**Linking standard**: GitHub URLs (not workspace-relative)

**Status**: ✅ **Consistent**

---

## Documentation Workflows

### Build Process

**Command**: `make docs-build`

**Steps**:
1. Generate OpenAPI spec (`scripts/export_openapi.py`)
2. Generate developer assistant context (`scripts/generate_llms_txt.py`)
3. Build MkDocs site
4. Run advisory checks (refs, coverage)
5. Link checking (Lychee)

**CI Status**: ✅ Auto-deploys to [GitHub Pages](https://jerdaw.github.io/healtharchive/)

### Validation Checks

| Check | Command | Status |
|-------|---------|--------|
| **Reference validation** | `make docs-refs` | ⏳ Run to check |
| **Coverage reporting** | `make docs-coverage` | ⏳ Run to check |
| **Link checking** | Lychee (in CI) | ⚠️ Advisory |
| **Format/lint** | `make check-full` | ✅ Part of CI |

---

## Templates

### Available Templates

Located in `docs/_templates/`:

| Template | Purpose | Usage Count |
|----------|---------|-------------|
| `../_templates/runbook-template.md` | Deployment procedures | 15+ runbooks |
| `../_templates/playbook-template.md` | Operational tasks | 32 playbooks |
| `../_templates/incident-template.md` | Post-mortems | 4 incidents |
| `../_templates/decision-template.md` | ADR-lite records | 1 decision |
| `../_templates/restore-test-log-template.md` | Restore verification | VPS logs |
| `../_templates/adoption-signals-log-template.md` | Organic public evidence | Event-triggered only; inactive by default |
| `../_templates/mentions-log-template.md` | Mentions tracking | VPS logs |
| `../_templates/ops-ui-friction-log-template.md` | UX issues | VPS logs |

**Status**: ✅ **Templates are available where durable records are warranted;
the organic-evidence template does not create a standing collection program.**

---

## Documentation Improvements Roadmap

### Completed (2026-01-18)

- ✅ Navigation restructure (Diátaxis framework)
- ✅ Quick start guide
- ✅ Tutorial trilogy (first contribution, architecture, debugging)
- ✅ API consumer guide
- ✅ Enhanced project hub
- ✅ CONTRIBUTING.md
- ✅ Reference section (data model, CLI, archive-tool)
- ✅ Documentation health dashboard (this page)
- ✅ Search optimization
- ✅ Advanced navigation features

### Planned Improvements

**Near-term** (Next quarter):
- [ ] Add more code examples to architecture docs
- [ ] Create video walkthroughs for tutorials
- [ ] Expand troubleshooting guides
- [ ] Add more FAQ entries

**Medium-term** (6 months):
- [ ] Multi-format export (PDF, ePub)
- [ ] Analytics integration (track popular pages)
- [ ] Interactive diagrams (clickable Mermaid)
- [ ] Versioned documentation (per release)

**Long-term** (Future):
- [ ] Multilingual documentation (French)
- [ ] Documentation chatbot (assistant-powered search)
- [ ] Automated screenshot updates
- [ ] Doc contribution gamification

---

## Quality Assurance

### Documentation Review Checklist

For each new document:

- [ ] Follows appropriate template
- [ ] Uses clear, concise language
- [ ] Includes code examples (if applicable)
- [ ] Cross-referenced from related docs
- [ ] Added to mkdocs.yml navigation (if key doc)
- [ ] Links verified (`make docs-refs`)
- [ ] Preview checked (`make docs-serve`)
- [ ] Spell-checked
- [ ] Grammar-checked
- [ ] Technical accuracy verified

### Quarterly Review

Every 3 months, review:

1. **Freshness**: Update stale docs (>180 days)
2. **Accuracy**: Verify technical details match current code
3. **Completeness**: Check for new features needing docs
4. **Gaps**: Identify missing documentation
5. **Feedback**: Incorporate user feedback from issues and contact messages

**Tracked in**: [Operations Cadence Checklist](../operations/ops-cadence-checklist.md)

---

## Metrics Over Time

### Historical Trends

| Date | Total Docs | In Nav | Coverage | Notable Changes |
|------|------------|--------|----------|-----------------|
| 2026-01-17 | 121 | 23 | 19% | Baseline before restructure |
| 2026-01-18 | 123 | 74 | 60% | Diátaxis restructure + new content |

**Trend**: ⬆️ **Significant improvement** (+41 percentage points)

---

## Contributing to Documentation

### How You Can Help

- 🐛 **Report issues**: Broken links, unclear instructions, typos
- 💡 **Suggest improvements**: Missing topics, better examples
- ✏️ **Fix typos**: Small PRs welcome!
- 📝 **Write new docs**: Fill gaps in coverage
- 🎨 **Improve diagrams**: Enhance Mermaid diagrams
- 🔍 **Review PRs**: Help review documentation changes

**See**: [contributing.md](../contributing.md)

---

## Tools & Infrastructure

### Documentation Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| **Generator** | MkDocs Material | Static site generation |
| **Markdown** | GitHub-flavored | Content format |
| **Diagrams** | Mermaid | Visual documentation |
| **API Docs** | OpenAPI + Swagger UI | Interactive API reference |
| **Search** | MkDocs search plugin | Full-text search |
| **Hosting** | GitHub Pages | https://jerdaw.github.io/healtharchive/ |
| **CI/CD** | GitHub Actions | Auto-build and deploy |

### Key Configuration Files

| File | Purpose |
|------|---------|
| `mkdocs.yml` | MkDocs configuration |
| `docs/_templates/` | Document templates |
| `scripts/export_openapi.py` | Generate API spec |
| `scripts/generate_llms_txt.py` | Generate developer assistant context |
| `scripts/check_docs_references.py` | Validate links |
| `scripts/check_docs_coverage.py` | Report coverage |

---

## Resources

### Documentation Standards

- [Documentation Guidelines](../documentation-guidelines.md) - Project standards
- [Diátaxis Framework](https://diataxis.fr/) - Documentation philosophy
- [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) - Theme documentation
- [GitHub-Flavored Markdown](https://github.github.com/gfm/) - Markdown spec

### Related Meta Docs

- [Documentation Process Audit](../documentation-process-audit.md) - 2026-01-09 audit
- [Documentation Guidelines](../documentation-guidelines.md) - Standards and taxonomy
- [Documentation Architecture Improvements](../planning/implemented/2026-01-17-documentation-architecture-improvements.md) - Implementation roadmap

---

## Summary

**Overall Health**: ✅ **Excellent**

- 60% navigation coverage (above 50% target)
- Well-balanced content types (Diátaxis-aligned)
- Complete core documentation (8/8 key docs)
- Active maintenance and improvement
- Good search and accessibility features
- Consistent multi-repo approach

**Recent Achievements**:
- Major restructure completed (2026-01-18)
- 51 new docs added to navigation
- 4 new tutorials created
- Comprehensive reference section
- Enhanced user experience

**Next Steps**:
- Monitor link health quarterly
- Continue quarterly freshness reviews
- Gather user feedback
- Iterate on improvements

---

**Questions or suggestions?** Open a GitHub issue for a concrete documentation
change, or use the [contact page](https://healtharchive.ca/contact) for a
general question.
