#include "Storage.h"
#include "SpiFlash.h"

extern MEM_CODE(unsigned char) Score[];

static uint8_t backendType = STORAGE_BACKEND_INTERNAL;

void storage_select_backend(uint8_t type)
{
    backendType = type == STORAGE_BACKEND_SPI ? STORAGE_BACKEND_SPI : STORAGE_BACKEND_INTERNAL;
}

uint8_t storage_get_backend(void)
{
    return backendType;
}

void storage_auto_detect(void)
{
    uint8_t jedec[3];
    SpiFlash_Init();
    SpiFlash_ReadJedecId(jedec);
    storage_select_backend(jedec[0] != 0xFF && jedec[0] != 0x00
                           ? STORAGE_BACKEND_SPI : STORAGE_BACKEND_INTERNAL);
}

void storage_init(void)
{
    if (backendType == STORAGE_BACKEND_SPI) SpiFlash_Init();
}

uint32_t storage_get_base_addr(void)
{
    return backendType == STORAGE_BACKEND_SPI ? SPI_FLASH_BASE_ADDR
                                               : (uint32_t)(uint16_t)Score;
}

void stream_init(MEM_XDATA(ScoreStream) *stream, uint32_t base, uint32_t size)
{
    stream->base = base;
    stream->pos = 0;
    stream->size = size;
}

void stream_sub(MEM_XDATA(ScoreStream) *stream, MEM_XDATA(ScoreStream) *parent,
                uint32_t offset, uint32_t size)
{
    stream->base = parent->base + offset;
    stream->pos = 0;
    stream->size = size;
}

uint8_t stream_u8(MEM_XDATA(ScoreStream) *stream, uint32_t offset)
{
    uint32_t address = stream->base + offset;
    if (backendType == STORAGE_BACKEND_SPI) return SpiFlash_ReadCached(address);
    return *((MEM_CODE(uint8_t) *)(uint16_t)address);
}

uint8_t stream_read(MEM_XDATA(ScoreStream) *stream, MEM_XDATA(uint8_t) *out)
{
    if (stream->pos >= stream->size) return 0;
    *out = stream_u8(stream, stream->pos);
    stream->pos++;
    return 1;
}

uint8_t stream_peek(MEM_XDATA(ScoreStream) *stream, MEM_XDATA(uint8_t) *out)
{
    if (stream->pos >= stream->size) return 0;
    *out = stream_u8(stream, stream->pos);
    return 1;
}

uint16_t stream_u16(MEM_XDATA(ScoreStream) *stream, uint32_t offset)
{
    uint16_t value = stream_u8(stream, offset);
    return value | ((uint16_t)stream_u8(stream, offset + 1) << 8);
}

uint32_t stream_u32(MEM_XDATA(ScoreStream) *stream, uint32_t offset)
{
    uint32_t value = stream_u8(stream, offset);
    value |= (uint32_t)stream_u8(stream, offset + 1) << 8;
    value |= (uint32_t)stream_u8(stream, offset + 2) << 16;
    value |= (uint32_t)stream_u8(stream, offset + 3) << 24;
    return value;
}
