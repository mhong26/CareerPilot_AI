interface StringListEditorProps {
  label: string
  values: string[]
  onChange: (next: string[]) => void
  placeholder?: string
}

// 可重用的 string[] 編輯器：skills / certifications / links / tech / bullets 共用。
export default function StringListEditor({
  label,
  values,
  onChange,
  placeholder,
}: StringListEditorProps) {
  function updateAt(index: number, value: string) {
    const next = [...values]
    next[index] = value
    onChange(next)
  }

  function removeAt(index: number) {
    onChange(values.filter((_, i) => i !== index))
  }

  function add() {
    onChange([...values, ''])
  }

  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium text-gray-700">{label}</label>
      {values.length === 0 && (
        <p className="text-sm text-gray-400">None yet.</p>
      )}
      {values.map((value, index) => (
        <div key={index} className="flex items-center gap-2">
          <input
            type="text"
            value={value}
            placeholder={placeholder}
            onChange={(event) => updateAt(index, event.target.value)}
            className="flex-1 rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
          />
          <button
            type="button"
            onClick={() => removeAt(index)}
            aria-label={`Remove ${label} item ${index + 1}`}
            className="rounded-md px-2 py-1 text-sm text-gray-400 hover:bg-gray-100 hover:text-red-500"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="text-sm text-blue-600 hover:underline"
      >
        + Add
      </button>
    </div>
  )
}
