/*
 * MyVectorInt.h
 *
 *  Created on: 26 Oct, 2015
 *      Author: jhgan
 */

#ifndef MYVECTORINT_H_
#define MYVECTORINT_H_

#include <stdlib.h>
#include <cstring>
#include <stdio.h>

/*
 *  The class of MyVector for integer.
 */
template<class T>
class MyVectorInt {
public:
	MyVectorInt();
	~MyVectorInt();

	/*
	 *  Add the given element to the back.
	 */
	void push_back(T _val);

	/*
	 *  Remove the last element from the array.
	 */
	void pop_back();

	/*
	 *  Allocate memory to the array with specified length.
	 */
	void reserve(unsigned int len);

	/*
	 *	Resize the array to specified length.
	 *	All the new elements are initialized by the given value.
	 */
	void resize(unsigned int targetSize, T _val);

	/*
	 *	Resize the array to specified length.
	 */
	void resize(unsigned int targetSize);

	/*
	 *  Shrink the array to fit the occupied size.
	 */
	void shrink_to_fit();

	/*
	 *  Swap the content with given vector.
	 */
	void swap(MyVectorInt<T>& _list);

	/*
	 *  Clear elements in the array but do not modify its capacity.
	 */
	void clear();

	/*
	 *  Release the space of the array, namely the array will be deleted.
	 */
	void release_space();

	/*
	 *  Return the size of the array.
	 */
	unsigned int size() const;

	/*
	 *  Return the capacity of the array.
	 */
	unsigned int capacity() const;

	/*
	 *  Return the element list.
	 */
	T* get_list() const;

	/*
	 *  Return the element at the given index.
	 */
	T& operator[](unsigned int i) const {
		if (i < this->size())
			return this->elementList[i + 2];
		else {
			printf(
					"Error in MyVectorInt accessing array element: Out of boundary! Accessing [%d] while size = %d and length = %d!\n",
					i, this->size(), this->capacity());
			exit(0);
		}
	}

protected:
//	/*
//	 *  The available length of this vector.
//	 */
//	unsigned int length;
//
//	/*
//	 *  The current number of elements.
//	 */
//	unsigned int elementNum;

	/*
	 *  The array of elements. If elementList != NULL,
	 *  elementList[0] is the elementNum and
	 *  elementList[1] is the length.
	 */
	T* elementList;

	/*
	 *  Enlarge the length of the array by a factor of 2.
	 */
	void enlarge();

};

#endif /* MYVECTORINT_H_ */
