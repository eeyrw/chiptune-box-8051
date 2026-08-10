#ifndef __STORAGE_H__
#define __STORAGE_H__

#include <stdint.h>
#include "Platform.h"

enum STORAGE_BACKEND
{
    STORAGE_BACKEND_INTERNAL = 0,
    STORAGE_BACKEND_SPI = 1,
};

typedef struct _ScoreStream
{
    uint32_t base;
    uint32_t pos;
    uint32_t size;
} ScoreStream;

void storage_select_backend(uint8_t type);
uint8_t storage_get_backend(void);
void storage_auto_detect(void);
void storage_init(void);
uint32_t storage_get_base_addr(void);

void stream_init(MEM_XDATA(ScoreStream) *stream, uint32_t base, uint32_t size);
void stream_sub(MEM_XDATA(ScoreStream) *stream, MEM_XDATA(ScoreStream) *parent,
                uint32_t offset, uint32_t size);
uint8_t stream_read(MEM_XDATA(ScoreStream) *stream, MEM_XDATA(uint8_t) *out);
uint8_t stream_peek(MEM_XDATA(ScoreStream) *stream, MEM_XDATA(uint8_t) *out);
uint8_t stream_u8(MEM_XDATA(ScoreStream) *stream, uint32_t offset);
uint16_t stream_u16(MEM_XDATA(ScoreStream) *stream, uint32_t offset);
uint32_t stream_u32(MEM_XDATA(ScoreStream) *stream, uint32_t offset);

#endif
