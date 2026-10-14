#!/usr/bin/env python3
"""
Memory File Chunking Utility

Breaks large memory files into logical chunks for better retrieval.
Supports semantic chunking (by headers) and fixed-size chunking.
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

MEMORY_DIR = Path.home() / '.claude/projects/memory'


def chunk_by_headers(content: str, max_chunk_size: int = 2000) -> list[dict]:
    """Split by markdown headers (semantic chunking)"""
    lines = content.split('\n')
    chunks = []
    current_chunk = []
    current_header = 'introduction'

    for line in lines:
        if line.startswith('#'):
            # New section found
            if current_chunk:
                chunks.append({
                    'header': current_header,
                    'content': '\n'.join(current_chunk),
                    'size': sum(len(l) for l in current_chunk)
                })
            current_header = line.lstrip('#').strip()
            current_chunk = [line]
        else:
            current_chunk.append(line)

            # Check if chunk is too large
            if sum(len(l) for l in current_chunk) > max_chunk_size:
                chunks.append({
                    'header': current_header,
                    'content': '\n'.join(current_chunk),
                    'size': sum(len(l) for l in current_chunk)
                })
                current_chunk = []

    # Add remaining chunk
    if current_chunk:
        chunks.append({
            'header': current_header,
            'content': '\n'.join(current_chunk),
            'size': sum(len(l) for l in current_chunk)
        })

    return chunks


def chunk_fixed_size(content: str, chunk_size: int = 1000) -> list[dict]:
    """Split into fixed-size chunks"""
    lines = content.split('\n')
    chunks = []

    for i in range(0, len(lines), chunk_size // 50):  # ~50 chars avg per line
        chunk_lines = lines[i:i + chunk_size // 50]
        chunk_text = '\n'.join(chunk_lines)

        # Extract title from first non-empty line
        title = next(
            (l.strip()[:80] for l in chunk_lines if l.strip() and not l.startswith('#')),
            f'chunk_{i // (chunk_size // 50)}'
        )

        chunks.append({
            'start_line': i,
            'end_line': min(i + chunk_size // 50, len(lines)),
            'title': title,
            'content': chunk_text,
            'size': len(chunk_text)
        })

    return chunks


def main():
    parser = argparse.ArgumentParser(
        description='Chunk memory files for better retrieval'
    )

    parser.add_argument('file', help='Memory file name (without .md)')
    parser.add_argument(
        '--method',
        choices=['headers', 'fixed'],
        default='headers',
        help='Chunking method: headers (semantic) or fixed-size'
    )
    parser.add_argument(
        '--size',
        type=int,
        default=2000,
        help='Max chunk size in characters'
    )
    parser.add_argument(
        '--save',
        action='store_true',
        help='Save chunk metadata to _chunks file'
    )

    args = parser.parse_args()

    # Read memory file
    file_path = MEMORY_DIR / f"{args.file}.md"
    if not file_path.exists():
        print(f"✗ File not found: {file_path}")
        return 1

    try:
        with open(file_path, 'r') as f:
            content = f.read()

        # Chunk the content
        if args.method == 'headers':
            chunks = chunk_by_headers(content, args.size)
        else:
            chunks = chunk_fixed_size(content, args.size)

        # Display chunks
        print(f"\n📦 Chunked {args.file}.md into {len(chunks)} chunks:")
        print("─" * 70)

        for i, chunk in enumerate(chunks):
            header = chunk.get('header') or chunk.get('title', f'Chunk {i}')
            size = chunk['size']
            print(f"\n  {i+1}. {header}")
            print(f"     Size: {size:,} chars")

        # Optionally save metadata
        if args.save:
            metadata = {
                'file': args.file,
                'method': args.method,
                'chunk_count': len(chunks),
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'chunks': chunks
            }

            chunks_file = MEMORY_DIR / f"_{args.file}_chunks.json"
            with open(chunks_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            print(f"\n✓ Chunk metadata saved to {chunks_file.name}")

        print("\n")
        return 0

    except Exception as e:
        print(f"✗ Error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
