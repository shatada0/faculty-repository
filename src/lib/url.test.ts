import { expect, test } from 'vitest'
import { isWebUrl, safeNext } from './url'

test('safeNext keeps only paths on this site', () => {
  expect(safeNext('/admin/works?type=vkr')).toBe('/admin/works?type=vkr')
  for (const bad of ['//evil.example', '/\\evil.example', 'https://evil.example', 'javascript:alert(1)', '', null]) {
    expect(safeNext(bad)).toBeNull()
  }
})

test('isWebUrl lets through only http(s) links', () => {
  const base = 'https://repo.example/works/1'
  expect(isWebUrl('https://files.repo.example/app/works/1.pdf?X-Amz-Signature=x', base)).toBe(true)
  expect(isWebUrl('/mock/sample.pdf', base)).toBe(true)
  expect(isWebUrl('javascript:alert(1)', base)).toBe(false)
  expect(isWebUrl(' JavaScript:alert(1)', base)).toBe(false)
  expect(isWebUrl('data:text/html,<script>alert(1)</script>', base)).toBe(false)
})
