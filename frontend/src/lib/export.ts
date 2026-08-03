// Artifact 匯出工具（FR-46）：structured content → markdown 字串，
// 加 clipboard 複製與 .md 檔下載。轉換函式是純函式（測試零 mock）。

import type {
  CoverLetterContent,
  InterviewPrepContent,
  TailoredResumeContent,
} from '../types/applicationKit'

export function tailoredResumeToMarkdown(content: TailoredResumeContent): string {
  const lines: string[] = ['# Tailored resume suggestions', '']
  if (content.overall_strategy) {
    lines.push(content.overall_strategy, '')
  }
  for (const section of content.section_suggestions) {
    lines.push(`## ${section.section || 'general'}`, '')
    for (const rewrite of section.bullet_rewrites) {
      if (rewrite.original) lines.push(`- **Original:** ${rewrite.original}`)
      lines.push(`- **Improved:** ${rewrite.improved}`)
      if (rewrite.reason) lines.push(`  - _Why:_ ${rewrite.reason}`)
    }
    if (section.keywords_to_add.length > 0) {
      lines.push(`- **Keywords to add:** ${section.keywords_to_add.join(', ')}`)
    }
    if (section.note) lines.push(`- **Note:** ${section.note}`)
    lines.push('')
  }
  if (content.top_keywords.length > 0) {
    lines.push(`**Top keywords:** ${content.top_keywords.join(', ')}`, '')
  }
  return lines.join('\n').trimEnd() + '\n'
}

export function coverLetterToMarkdown(content: CoverLetterContent): string {
  const paragraphs = [content.intro, ...content.body_paragraphs, content.closing].filter(Boolean)
  return '# Cover letter\n\n' + paragraphs.join('\n\n') + '\n'
}

export function interviewPrepToMarkdown(content: InterviewPrepContent): string {
  const lines: string[] = ['# Interview preparation', '']
  content.questions.forEach((q, i) => {
    lines.push(`## ${i + 1}. ${q.question}`)
    if (q.category) lines.push(`- **Category:** ${q.category}`)
    if (q.why_it_matters) lines.push(`- **Why it matters:** ${q.why_it_matters}`)
    if (q.related_resume_area) lines.push(`- **Related resume area:** ${q.related_resume_area}`)
    if (q.answer_outline.length > 0) {
      lines.push('- **Answer outline:**')
      for (const point of q.answer_outline) lines.push(`  - ${point}`)
    }
    lines.push('')
  })
  return lines.join('\n').trimEnd() + '\n'
}

export async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text)
}

// Blob + 隱形 <a download> 下載；用完立即 revoke 釋放 object URL。
export function downloadMarkdown(filename: string, markdown: string): void {
  const blob = new Blob([markdown], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
