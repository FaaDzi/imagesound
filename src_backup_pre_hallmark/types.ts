export type FileMode = 'classic' | 'vibe';
export type MediaType = 'audio' | 'image';
export type OutputFormat = 'wav' | 'mp3' | 'ogg' | 'm4a';

export interface ConvertedFile {
  id: string;
  originalName: string;
  type: MediaType;
  thumbnailUrl?: string; // For images
  duration?: string; // e.g. "03:45"
  outputFormat: OutputFormat;
  daysRemaining: number;
  timestamp: string;
}

export const MOCK_LIBRARY: ConvertedFile[] = [
  {
    id: '1',
    originalName: 'sector_scan.png',
    type: 'image',
    thumbnailUrl: 'https://images.unsplash.com/photo-1542385151-efd9000785a0?auto=format&fit=crop&q=80&w=200',
    duration: '01:14',
    outputFormat: 'wav',
    daysRemaining: 6,
    timestamp: new Date(Date.now() - 86400000).toISOString()
  },
  {
    id: '2',
    originalName: 'mainframe_dump.mp3',
    type: 'audio',
    duration: '05:32',
    outputFormat: 'ogg',
    daysRemaining: 4,
    timestamp: new Date(Date.now() - 3 * 86400000).toISOString()
  },
  {
    id: '3',
    originalName: 'neural_pattern.jpg',
    type: 'image',
    thumbnailUrl: 'https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&q=80&w=200',
    duration: '02:08',
    outputFormat: 'm4a',
    daysRemaining: 1,
    timestamp: new Date(Date.now() - 6 * 86400000).toISOString()
  }
];
