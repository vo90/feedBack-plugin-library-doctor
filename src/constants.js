export const API_ROOT = '/api/plugins/library_doctor';
export const PAGE_SIZE = 50;
export const SONG_TOOL_PAGE_SIZE = 24;
export const WORKER_MODE_KEY = 'library_doctor.scan.worker_mode';
export const WORKER_LIMIT_KEY = 'library_doctor.scan.worker_limit';
export const LEGACY_LAYOUT_QUERY = 'libraryDoctorLayout';

export const SUPPORTED_HOST = Object.freeze({
  minVersion: '0.3.0-alpha.1',
  moduleContract: 'native-es-modules-v1',
});
