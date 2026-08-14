#ifndef SCORE_FLASH_H
#define SCORE_FLASH_H

#include <stdint.h>
#include "Platform.h"

extern MEM_CODE(unsigned char) Score[];
extern MEM_CODE(uint32_t) ScoreSize;

/* Absolute Code-Flash address readers (internal MOVC only). */
uint8_t score_u8(uint16_t addr);
uint16_t score_u16(uint16_t addr);
uint32_t score_u32(uint16_t addr);
void score_copy_xdata(uint16_t codeAddr, uint8_t __xdata *dst, uint8_t len);
/* Fixed 48-byte instrument/header copy: DPTR=code, stack dst only. */
void score_copy48_xdata(uint16_t codeAddr, uint8_t __xdata *dst);

#endif
