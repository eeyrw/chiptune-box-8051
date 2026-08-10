#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "RegisterDefine.h"
/*
 * Redirect stdout to UART
 */
int putchar(int c)
{
	uint8_t serial_irq = ES;
	ES = 0;

	if (c == '\n')
	{
		SBUF = '\r';
		while (!TI)
			;
		TI = 0;
		SBUF = '\n';
		while (!TI)
			;
		TI = 0;
	}
	else
	{
		SBUF = c;
		while (!TI)
			;
		TI = 0;
	}

	ES = serial_irq;
	return c;
}

/*
 * Redirect stdin to UART
 */
int getchar()
{
	//while (!RI)
	//	;
	//RI = 0;
	return SBUF;
}
