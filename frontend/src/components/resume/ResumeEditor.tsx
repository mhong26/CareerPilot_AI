import type { ReactNode } from 'react'

import type {
  EducationItem,
  ExperienceItem,
  ProjectItem,
  ResumeParsed,
} from '../../types/resume'
import StringListEditor from './StringListEditor'

interface ResumeEditorProps {
  value: ResumeParsed
  onChange: (next: ResumeParsed) => void
}

// 共用小元件：帶 label 的單行文字輸入。
function Field({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
      />
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-t border-gray-100 pt-5">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-500">{title}</h3>
      {children}
    </section>
  )
}

const emptyExperience = (): ExperienceItem => ({
  company: '',
  title: '',
  start_date: '',
  end_date: '',
  bullets: [],
})
const emptyProject = (): ProjectItem => ({ name: '', description: '', bullets: [], tech: [] })
const emptyEducation = (): EducationItem => ({
  school: '',
  degree: '',
  field: '',
  graduation: '',
})

export default function ResumeEditor({ value, onChange }: ResumeEditorProps) {
  // 不可變更新整包 ResumeParsed 的單一欄位。
  function patch<K extends keyof ResumeParsed>(key: K, v: ResumeParsed[K]) {
    onChange({ ...value, [key]: v })
  }

  // 陣列項目通用：改第 i 筆 / 刪第 i 筆 / 新增一筆。
  function updateItem<T>(key: keyof ResumeParsed, index: number, item: T) {
    const list = [...(value[key] as T[])]
    list[index] = item
    onChange({ ...value, [key]: list })
  }
  function removeItem(key: keyof ResumeParsed, index: number) {
    const list = (value[key] as unknown[]).filter((_, i) => i !== index)
    onChange({ ...value, [key]: list })
  }
  function addItem<T>(key: keyof ResumeParsed, item: T) {
    const list = [...(value[key] as T[]), item]
    onChange({ ...value, [key]: list })
  }

  return (
    <div className="space-y-5">
      <Section title="Basic info">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Name" value={value.basic_info.name} onChange={(v) => patch('basic_info', { ...value.basic_info, name: v })} />
          <Field label="Email" value={value.basic_info.email} onChange={(v) => patch('basic_info', { ...value.basic_info, email: v })} />
          <Field label="Phone" value={value.basic_info.phone} onChange={(v) => patch('basic_info', { ...value.basic_info, phone: v })} />
          <Field label="Location" value={value.basic_info.location} onChange={(v) => patch('basic_info', { ...value.basic_info, location: v })} />
        </div>
        <StringListEditor
          label="Links"
          values={value.basic_info.links}
          onChange={(links) => patch('basic_info', { ...value.basic_info, links })}
          placeholder="https://..."
        />
      </Section>

      <Section title="Summary">
        <textarea
          value={value.summary}
          onChange={(event) => patch('summary', event.target.value)}
          rows={4}
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        />
      </Section>

      <Section title="Skills">
        <StringListEditor label="Skills" values={value.skills} onChange={(v) => patch('skills', v)} />
      </Section>

      <Section title="Experience">
        {value.experience.map((item, index) => (
          <div key={index} className="space-y-3 rounded-md border border-gray-200 p-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Company" value={item.company} onChange={(v) => updateItem('experience', index, { ...item, company: v })} />
              <Field label="Title" value={item.title} onChange={(v) => updateItem('experience', index, { ...item, title: v })} />
              <Field label="Start" value={item.start_date} onChange={(v) => updateItem('experience', index, { ...item, start_date: v })} />
              <Field label="End" value={item.end_date} onChange={(v) => updateItem('experience', index, { ...item, end_date: v })} />
            </div>
            <StringListEditor
              label="Bullets"
              values={item.bullets}
              onChange={(bullets) => updateItem('experience', index, { ...item, bullets })}
            />
            <button type="button" onClick={() => removeItem('experience', index)} className="text-sm text-red-500 hover:underline">
              Remove this experience
            </button>
          </div>
        ))}
        <button type="button" onClick={() => addItem('experience', emptyExperience())} className="text-sm text-blue-600 hover:underline">
          + Add experience
        </button>
      </Section>

      <Section title="Projects">
        {value.projects.map((item, index) => (
          <div key={index} className="space-y-3 rounded-md border border-gray-200 p-4">
            <Field label="Name" value={item.name} onChange={(v) => updateItem('projects', index, { ...item, name: v })} />
            <div>
              <label className="block text-sm font-medium text-gray-700">Description</label>
              <textarea
                value={item.description}
                onChange={(event) => updateItem('projects', index, { ...item, description: event.target.value })}
                rows={2}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
              />
            </div>
            <StringListEditor
              label="Bullets"
              values={item.bullets}
              onChange={(bullets) => updateItem('projects', index, { ...item, bullets })}
            />
            <StringListEditor
              label="Tech"
              values={item.tech}
              onChange={(tech) => updateItem('projects', index, { ...item, tech })}
            />
            <button type="button" onClick={() => removeItem('projects', index)} className="text-sm text-red-500 hover:underline">
              Remove this project
            </button>
          </div>
        ))}
        <button type="button" onClick={() => addItem('projects', emptyProject())} className="text-sm text-blue-600 hover:underline">
          + Add project
        </button>
      </Section>

      <Section title="Education">
        {value.education.map((item, index) => (
          <div key={index} className="space-y-3 rounded-md border border-gray-200 p-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="School" value={item.school} onChange={(v) => updateItem('education', index, { ...item, school: v })} />
              <Field label="Degree" value={item.degree} onChange={(v) => updateItem('education', index, { ...item, degree: v })} />
              <Field label="Field" value={item.field} onChange={(v) => updateItem('education', index, { ...item, field: v })} />
              <Field label="Graduation" value={item.graduation} onChange={(v) => updateItem('education', index, { ...item, graduation: v })} />
            </div>
            <button type="button" onClick={() => removeItem('education', index)} className="text-sm text-red-500 hover:underline">
              Remove this education
            </button>
          </div>
        ))}
        <button type="button" onClick={() => addItem('education', emptyEducation())} className="text-sm text-blue-600 hover:underline">
          + Add education
        </button>
      </Section>

      <Section title="Certifications">
        <StringListEditor label="Certifications" values={value.certifications} onChange={(v) => patch('certifications', v)} />
      </Section>
    </div>
  )
}
