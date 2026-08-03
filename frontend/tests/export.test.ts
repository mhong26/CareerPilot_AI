// 匯出轉換純函式測試（clipboard / 下載不進 jsdom，只驗 markdown 組裝）。
import { describe, expect, it } from 'vitest'

import {
  coverLetterToMarkdown,
  interviewPrepToMarkdown,
  tailoredResumeToMarkdown,
} from '../src/lib/export'

describe('artifact markdown export', () => {
  it('renders tailored resume suggestions with sections and rewrites', () => {
    const md = tailoredResumeToMarkdown({
      overall_strategy: 'Lead with backend work.',
      section_suggestions: [
        {
          section: 'experience',
          bullet_rewrites: [
            { original: 'Built APIs', improved: 'Built REST APIs serving 1M req/day', reason: 'Matches scale requirement' },
          ],
          keywords_to_add: ['Python', 'Go'],
          note: '',
        },
      ],
      top_keywords: ['Python'],
    })

    expect(md).toContain('# Tailored resume suggestions')
    expect(md).toContain('Lead with backend work.')
    expect(md).toContain('## experience')
    expect(md).toContain('**Original:** Built APIs')
    expect(md).toContain('**Improved:** Built REST APIs serving 1M req/day')
    expect(md).toContain('**Keywords to add:** Python, Go')
    expect(md).toContain('**Top keywords:** Python')
  })

  it('renders cover letter paragraphs in order, skipping empties', () => {
    const md = coverLetterToMarkdown({
      intro: 'Dear team,',
      body_paragraphs: ['I built APIs.', ''],
      closing: 'Thanks.',
    })

    expect(md).toBe('# Cover letter\n\nDear team,\n\nI built APIs.\n\nThanks.\n')
  })

  it('renders interview questions with outline bullets', () => {
    const md = interviewPrepToMarkdown({
      questions: [
        {
          question: 'Why us?',
          category: 'behavioral',
          why_it_matters: 'Tests motivation.',
          related_resume_area: 'Summary',
          answer_outline: ['Mention mission', 'Tie to experience'],
        },
      ],
    })

    expect(md).toContain('## 1. Why us?')
    expect(md).toContain('**Category:** behavioral')
    expect(md).toContain('- **Answer outline:**')
    expect(md).toContain('  - Mention mission')
  })
})
